from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import case, desc, func, select

from adintel.core.settings import ROOT_DIR, get_settings
from adintel.db.models import (
    OtterlyCitationRecord,
    OtterlyPromptRecord,
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


def _to_float(val):
    """Safely convert Decimal or numeric values to float for JSON serialization."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    if hasattr(val, "__float__"):
        return float(val)
    return val


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


def _build_timeseries(session, advertiser_name: str, metric: str, country: str, days: int) -> list[dict]:
    """Return chronological list of data-point dicts for a metric."""
    model, date_col_name, value_cols = _METRIC_MAP[metric]
    date_col = getattr(model, date_col_name)

    q = select(model).where(model.advertiser_name == advertiser_name)
    if hasattr(model, "country"):
        q = q.where(model.country == country)

    rows = session.scalars(
        q.order_by(desc(date_col)).limit(days)
    ).all()

    points = []
    for row in reversed(rows):
        point = {"date": getattr(row, date_col_name).isoformat()}
        if hasattr(row, "country"):
            point["country"] = row.country
        for col in value_cols:
            point[col] = _to_float(getattr(row, col, None))
        points.append(point)
    return points


def _resolved_info(advertiser) -> dict:
    """Return resolution metadata if fuzzy auto-resolve was used."""
    resolved_from = getattr(advertiser, "_resolved_from", None)
    if resolved_from:
        return {"resolved_from": resolved_from, "resolved_to": advertiser.name}
    return {}


def _build_summary(advertiser_name: str, country: str | None = None) -> dict:
    with _session_factory()() as session:
        advertisers = AdvertiserRepository(session)
        advertiser = advertisers.resolve(advertiser_name)
        if advertiser is None:
            suggestions = advertisers.suggest(advertiser_name)
            return {
                "found": False,
                "advertiser_name": advertiser_name,
                "message": (
                    f"No advertiser found for '{advertiser_name}'. Did you mean: "
                    + ", ".join(f"'{s}'" for s in suggestions)
                    + "?"
                ) if suggestions else f"No advertiser found for '{advertiser_name}'.",
                "did_you_mean": suggestions,
            }

        # Use the canonical name from the database for all queries
        canonical_name = advertiser.name

        def _q(model):
            """Build a base query with advertiser + optional country filter."""
            q = select(model).where(model.advertiser_name == canonical_name)
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
        **_resolved_info(advertiser),
        "advertiser": advertiser.model_dump(),
        "sensortower": {
            "latest_download": (
                {
                    "period_date": latest_download.period_date.isoformat(),
                    "downloads": latest_download.downloads,
                    "revenue": _to_float(latest_download.revenue),
                    "country": latest_download.country,
                    "os": latest_download.os,
                }
                if latest_download
                else None
            ),
            "latest_usage": (
                {
                    "period_date": latest_usage.period_date.isoformat(),
                    "avg_dau": _to_float(latest_usage.avg_dau),
                    "time_spent_min": _to_float(latest_usage.time_spent_min),
                    "sessions_per_day": _to_float(latest_usage.sessions_per_day),
                    "country": latest_usage.country,
                }
                if latest_usage
                else None
            ),
            "latest_retention": (
                {
                    "cohort_date": latest_retention.cohort_date.isoformat(),
                    "d1": _to_float(latest_retention.d1),
                    "d7": _to_float(latest_retention.d7),
                    "d30": _to_float(latest_retention.d30),
                    "country": latest_retention.country,
                }
                if latest_retention
                else None
            ),
            "latest_impression_share": (
                {
                    "period_date": latest_impression_share.period_date.isoformat(),
                    "network": latest_impression_share.network,
                    "sov_pct": _to_float(latest_impression_share.sov_pct),
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
                    "male_pct": _to_float(row.male_pct),
                    "female_pct": _to_float(row.female_pct),
                    "country": row.country,
                }
                for row in demographics
            ],
            "latest_reviews": (
                {
                    "period_date": latest_reviews.period_date.isoformat(),
                    "avg_rating": _to_float(latest_reviews.avg_rating),
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
                    "star_rating": _to_float(row.star_rating),
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
                    "traffic_score": _to_float(row.traffic_score),
                    "opportunity_score": _to_float(row.opportunity_score),
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
            "AdIntel provides competitive intelligence for mobile advertisers via SensorTower "
            "and GEO (Generative Engine Optimization) analysis via Otterly.AI.\n\n"
            "DATA COLLECTED FROM SENSORTOWER (per tracked advertiser):\n"
            "- Downloads & revenue: daily timeseries, up to 90 days, by country and OS.\n"
            "- DAU, time spent, sessions per day: daily engagement metrics.\n"
            "- Retention cohorts: D1/D3/D7/D14/D30/D60 by month.\n"
            "- Impression share (Share of Voice): per ad network (23 networks), daily.\n"
            "- Demographics: age brackets (18-24, 25-34, 35-44, 45-54, 55+) with male/female %.\n"
            "- Rankings: monthly position in category chart by ad SOV.\n"
            "- Reviews: daily aggregate ratings and individual review text with sentiment.\n"
            "- Ad creatives: type, network, thumbnail URL, duration bucket, first seen date.\n"
            "- ASO keywords: rank, traffic score, opportunity score per device and country.\n"
            "- Market-wide top apps: category rankings with downloads, revenue, DAU, and "
            "per-network ad presence flags for ALL apps (not just tracked ones). "
            "Populated by running 'adintel collect market-top-apps'.\n\n"
            "DATA COLLECTED FROM OTTERLY.AI (GEO):\n"
            "- AI search visibility: citation rate per prompt across ChatGPT, Perplexity, "
            "Google AI Overview, Google Gemini, Microsoft Copilot.\n"
            "- Prompt-level data: volume, brand rank, sentiment, competitor overlap.\n"
            "- Citation data: cited URLs and domains, brand vs third-party, domain category.\n\n"
            "ADVERTISER NAME RESOLUTION:\n"
            "- Names are fuzzy-matched: 'chime', 'Chme', or 'chim' all resolve to 'Chime'.\n"
            "- When auto-resolved, the response includes resolved_from and resolved_to fields.\n"
            "- Use list_advertisers to see all tracked advertisers with data freshness metadata.\n\n"
            "SENSORTOWER WORKFLOW:\n"
            "- Competitive comparison (2+ advertisers) → call get_full_comparison.\n"
            "  Returns 30-day timeseries for downloads and DAU, per-network SOV trends,\n"
            "  and a server-computed gap_analysis (exclusive networks, SOV ratios, efficiency,\n"
            "  opportunity bullets).\n\n"
            "- Single advertiser deep dive → call get_advertiser_summary.\n"
            "  Returns all latest data: demographics, reviews, creatives, ASO keywords.\n\n"
            "- Custom date range or individual metric → use get_metric_timeseries.\n"
            "  Metrics: downloads, usage, retention, impression_share, rankings, reviews.\n\n"
            "- Market-wide category rankings → call get_market_top_apps.\n"
            "  Sort by: downloads, revenue, dau, impression_share, rank.\n"
            "  Filter by network presence (e.g. only apps advertising on TikTok).\n\n"
            "- Custom analysis → call run_query with a SELECT statement.\n"
            "  Call read_schema_text first to understand available tables and columns.\n"
            "  Limited to 100 rows; read-only (SELECT/WITH only).\n\n"
            "GEO (AI SEARCH VISIBILITY) WORKFLOW:\n"
            "- Single brand AI visibility → call get_geo_visibility_summary.\n"
            "  Shows visibility rate, engine breakdown, sentiment, top cited domains.\n\n"
            "- Compare 2+ brands in AI search → call compare_geo_visibility.\n"
            "  Shows blind spots, engine gaps, competitor overlap, opportunities.\n\n"
            "- Citation deep dive → call get_geo_citation_analysis.\n"
            "  Shows which URLs/domains get cited, brand vs third-party split, categories.\n\n"
            "- Prompt/query analysis → call get_geo_prompt_insights.\n"
            "  Shows top queries, ranking, blind-spot prompts where competitors win.\n\n"
            "COLLECTION HEALTH:\n"
            "- get_collection_health / get_collection_alerts: check data freshness and failures.\n"
            "- get_recent_collection_runs: inspect per-metric outcomes of past scrape runs.\n"
            "- list_advertisers: shows st_last_scraped, st_download_rows, geo_last_scraped per brand.\n\n"
            "COMPETITIVE GAP ANALYSIS REPORT FORMAT:\n"
            "1. Executive summary (who leads, by how much)\n"
            "2. Side-by-side metrics table (downloads, DAU, revenue, total SOV)\n"
            "3. Ad placement gap: networks each advertiser uses exclusively\n"
            "4. Network efficiency: downloads-per-SOV-point comparison\n"
            "5. Opportunities: untapped networks, underweight channels, strategic moves\n\n"
            "GEO ANALYSIS REPORT FORMAT:\n"
            "1. Visibility overview: cited rate across AI engines\n"
            "2. Engine-by-engine breakdown with sentiment and rank\n"
            "3. Blind spots: engines/prompts where competitors appear but brand doesn't\n"
            "4. Citation landscape: top domains, brand-owned vs third-party\n"
            "5. Opportunities: uncovered engines, high-volume uncited prompts, negative sentiment areas"
        ),
        streamable_http_path="/",
        stateless_http=is_vercel,
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
        description=(
            "List advertisers currently stored in AdIntel with data freshness metadata "
            "(last scraped timestamps and row counts)."
        ),
    )
    def list_advertisers() -> str:
        from adintel.db.models import AdvertiserRecord, ScrapeRunRecord

        with _session_factory()() as session:
            advertisers = AdvertiserRepository(session).list()

            # SensorTower freshness: latest successful run per advertiser
            st_freshness_q = (
                select(
                    ScrapeRunRecord.advertiser_name,
                    func.max(ScrapeRunRecord.finished_at).label("last_scraped"),
                    func.count().label("total_runs"),
                )
                .where(ScrapeRunRecord.platform == "sensortower")
                .where(ScrapeRunRecord.status.in_(["success", "partial"]))
                .group_by(ScrapeRunRecord.advertiser_name)
            )
            st_freshness = {
                row.advertiser_name: {
                    "last_scraped": row.last_scraped.isoformat() if row.last_scraped else None,
                    "total_runs": row.total_runs,
                }
                for row in session.execute(st_freshness_q).all()
            }

            # Download row counts per advertiser
            dl_counts_q = (
                select(
                    SensorTowerDownloadRecord.advertiser_name,
                    func.count().label("row_count"),
                )
                .group_by(SensorTowerDownloadRecord.advertiser_name)
            )
            dl_counts = {
                row.advertiser_name: row.row_count
                for row in session.execute(dl_counts_q).all()
            }

            # GEO freshness: latest scraped_at from otterly prompts per target
            geo_freshness_q = (
                select(
                    OtterlyPromptRecord.target_brand_or_domain_name,
                    func.max(OtterlyPromptRecord.scraped_at).label("last_scraped"),
                )
                .group_by(OtterlyPromptRecord.target_brand_or_domain_name)
            )
            geo_freshness = {
                row.target_brand_or_domain_name: row.last_scraped.isoformat() if row.last_scraped else None
                for row in session.execute(geo_freshness_q).all()
            }

        result = []
        for adv in advertisers:
            d = adv.model_dump()
            name = adv.name
            domain = adv.domain

            st = st_freshness.get(name, {})
            geo_last = geo_freshness.get(domain) if domain else geo_freshness.get(name)

            d["data_freshness"] = {
                "st_last_scraped": st.get("last_scraped"),
                "st_total_runs": st.get("total_runs", 0),
                "st_download_rows": dl_counts.get(name, 0),
                "geo_last_scraped": geo_last,
            }
            result.append(d)

        return json.dumps({"advertisers": result}, indent=2)

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
        name="run_query",
        description=(
            "Execute a read-only SQL query against the AdIntel database. "
            "Only SELECT statements are allowed. Read schema://adintel first to understand the table structure. "
            "Returns up to 100 rows."
        ),
    )
    def run_query(sql: str) -> str:
        import re
        from sqlalchemy import text

        stripped = sql.strip()
        upper = stripped.upper()

        # Must start with SELECT or WITH (CTE)
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            return json.dumps({"error": "Only SELECT queries are allowed."})

        # Reject DML/DDL keywords as standalone words
        forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b"
        if re.search(forbidden, upper):
            return json.dumps({"error": "Only SELECT queries are allowed. Detected forbidden keyword."})

        max_rows = 100
        try:
            with _session_factory()() as session:
                conn = session.connection()
                conn.execute(text("SET TRANSACTION READ ONLY"))
                result = conn.execute(text(stripped))
                columns = list(result.keys())
                rows = []
                truncated = False
                for i, row in enumerate(result):
                    if i >= max_rows:
                        truncated = True
                        break
                    rows.append({
                        col: (
                            _to_float(val) if isinstance(val, Decimal) else
                            val.isoformat() if isinstance(val, (date, datetime)) else
                            val
                        )
                        for col, val in zip(columns, row)
                    })
                return json.dumps({
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                }, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

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
            repo = AdvertiserRepository(session)
            resolved = repo.resolve(advertiser_name)
            if resolved is None:
                suggestions = repo.suggest(advertiser_name)
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": (
                        f"No advertiser found for '{advertiser_name}'. Did you mean: "
                        + ", ".join(f"'{s}'" for s in suggestions) + "?"
                    ) if suggestions else f"No advertiser found for '{advertiser_name}'.",
                    "did_you_mean": suggestions,
                }, indent=2)
            canonical_name = resolved.name
            q = (
                select(model)
                .where(model.advertiser_name == canonical_name)
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
                point[col] = _to_float(getattr(row, col, None))
            data_points.append(point)

        return json.dumps({
            **_resolved_info(resolved),
            "advertiser_name": canonical_name,
            "metric": metric,
            "country": country,
            "count": len(data_points),
            "data": data_points,
        }, indent=2)

    @server.tool(
        name="get_full_comparison",
        description=(
            "Full competitive comparison for two or more advertisers. "
            "Returns 30-day timeseries for downloads and DAU, per-network ad impression share with trends, "
            "and a server-computed gap_analysis with exclusive networks, SOV ratios, efficiency indicators, "
            "and opportunity bullets. Use this for any competitive analysis task."
        ),
    )
    def get_full_comparison(
        advertiser_names: str,
        country: str = "US",
        days: int = 30,
    ) -> str:
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if not names:
            return json.dumps({"error": "No advertiser names provided."})
        days = max(1, min(days, 90))

        advertiser_data: dict[str, dict] = {}

        with _session_factory()() as session:
            for name in names:
                # ── Snapshot ──────────────────────────────────────────
                repo = AdvertiserRepository(session)
                advertiser = repo.resolve(name)
                if advertiser is None:
                    suggestions = repo.suggest(name)
                    advertiser_data[name] = {
                        "found": False,
                        "message": (
                            f"No advertiser found for '{name}'. Did you mean: "
                            + ", ".join(f"'{s}'" for s in suggestions) + "?"
                        ) if suggestions else f"No advertiser found for '{name}'.",
                        "did_you_mean": suggestions,
                    }
                    continue

                canonical_name = advertiser.name

                def _q(model, adv_name=canonical_name):
                    q = select(model).where(model.advertiser_name == adv_name)
                    if hasattr(model, "country"):
                        q = q.where(model.country == country)
                    return q

                latest_dl = session.scalar(
                    _q(SensorTowerDownloadRecord)
                    .order_by(desc(SensorTowerDownloadRecord.period_date))
                )
                latest_usage = session.scalar(
                    _q(SensorTowerUsageRecord)
                    .order_by(desc(SensorTowerUsageRecord.period_date))
                )
                latest_imp = session.scalar(
                    _q(SensorTowerImpressionShareRecord)
                    .where(SensorTowerImpressionShareRecord.network == "all")
                    .order_by(desc(SensorTowerImpressionShareRecord.period_date))
                )

                snapshot = {
                    "downloads": latest_dl.downloads if latest_dl else None,
                    "revenue": _to_float(latest_dl.revenue) if latest_dl else None,
                    "downloads_date": latest_dl.period_date.isoformat() if latest_dl else None,
                    "avg_dau": _to_float(latest_usage.avg_dau) if latest_usage else None,
                    "sessions_per_day": _to_float(latest_usage.sessions_per_day) if latest_usage else None,
                    "usage_date": latest_usage.period_date.isoformat() if latest_usage else None,
                    "total_sov": _to_float(latest_imp.sov_pct) if latest_imp else None,
                    "sov_date": latest_imp.period_date.isoformat() if latest_imp else None,
                }

                # ── Timeseries: downloads & usage ─────────────────────
                dl_series = _build_timeseries(session, canonical_name, "downloads", country, days)
                usage_series = _build_timeseries(session, canonical_name, "usage", country, days)

                # ── Per-network impression share ───────────────────────
                imp_rows = session.scalars(
                    select(SensorTowerImpressionShareRecord)
                    .where(SensorTowerImpressionShareRecord.advertiser_name == canonical_name)
                    .where(SensorTowerImpressionShareRecord.country == country)
                    .where(SensorTowerImpressionShareRecord.network != "all")
                    .where(SensorTowerImpressionShareRecord.network != "other")
                    .order_by(desc(SensorTowerImpressionShareRecord.period_date))
                    .limit(days * 20)  # enough rows for all networks × days
                ).all()

                # Group by network → latest SOV + recent trend
                networks_map: dict[str, dict] = {}
                for row in imp_rows:
                    net = row.network
                    sov = _to_float(row.sov_pct) or 0.0
                    dt = row.period_date.isoformat()
                    if net not in networks_map:
                        networks_map[net] = {"latest_sov": sov, "latest_date": dt, "trend": []}
                    networks_map[net]["trend"].append({"date": dt, "sov_pct": sov})

                # Trim trend to `days` entries and sort chronologically
                for net in networks_map:
                    networks_map[net]["trend"] = sorted(
                        networks_map[net]["trend"], key=lambda x: x["date"]
                    )[-days:]

                advertiser_data[canonical_name] = {
                    "found": True,
                    **_resolved_info(advertiser),
                    "snapshot": snapshot,
                    "timeseries": {
                        "downloads": dl_series,
                        "usage": usage_series,
                    },
                    "ad_placement": {
                        "networks": networks_map,
                        "total_sov": snapshot["total_sov"],
                        "network_count": len(networks_map),
                    },
                }

        # ── Gap analysis (computed across all advertisers) ─────────────
        found = {n: d for n, d in advertiser_data.items() if d.get("found")}

        gap_analysis: dict = {}
        if len(found) >= 2:
            # Network coverage sets
            network_sets: dict[str, set] = {
                n: {net for net, v in d["ad_placement"]["networks"].items() if v["latest_sov"] > 0}
                for n, d in found.items()
            }
            all_nets = set().union(*network_sets.values())
            shared = set.intersection(*network_sets.values()) if network_sets else set()
            exclusive: dict[str, list] = {
                n: sorted(network_sets[n] - shared) for n in found
            }

            # SOV comparison
            sov_totals = {n: d["snapshot"]["total_sov"] or 0.0 for n, d in found.items()}
            sov_leader = max(sov_totals, key=lambda n: sov_totals[n])
            sov_follower = min(sov_totals, key=lambda n: sov_totals[n])
            sov_ratio = (
                round(sov_totals[sov_leader] / sov_totals[sov_follower], 1)
                if sov_totals[sov_follower] > 0 else None
            )

            # Efficiency: downloads per SOV point
            efficiency: dict[str, float | None] = {}
            for n, d in found.items():
                dl = d["snapshot"]["downloads"]
                sov = d["snapshot"]["total_sov"]
                efficiency[n] = round(dl / sov, 1) if dl and sov and sov > 0 else None

            # Opportunity bullets
            opportunities: list[str] = []
            for n, d in found.items():
                others = [o for o in found if o != n]
                for other in others:
                    for net in exclusive.get(other, []):
                        other_sov = found[other]["ad_placement"]["networks"].get(net, {}).get("latest_sov", 0)
                        if other_sov and other_sov > 0:
                            opportunities.append(
                                f"{n} has no presence on {net} — {other}'s active network (SOV {other_sov:.4%})"
                            )

            # Top network for each advertiser
            for n, d in found.items():
                nets = d["ad_placement"]["networks"]
                if nets:
                    top_net = max(nets, key=lambda x: nets[x]["latest_sov"])
                    top_sov = nets[top_net]["latest_sov"]
                    opportunities.append(
                        f"{n}'s strongest network is {top_net} (SOV {top_sov:.4%})"
                    )

            gap_analysis = {
                "network_coverage": {
                    n: {"exclusive": exclusive[n], "shared_with_others": sorted(shared)}
                    for n in found
                },
                "all_networks_seen": sorted(all_nets),
                "sov_comparison": {
                    "leader": sov_leader,
                    "totals": {n: round(v, 6) for n, v in sov_totals.items()},
                    "ratio": sov_ratio,
                    "insight": (
                        f"{sov_leader} commands {sov_ratio}x the total impression share of {sov_follower}."
                        if sov_ratio else "SOV data unavailable for comparison."
                    ),
                },
                "efficiency_indicators": {
                    n: {"downloads_per_sov_point": efficiency[n]} for n in found
                },
                "opportunities": opportunities,
            }

        return json.dumps(
            {
                "comparison_date": date.today().isoformat(),
                "country": country,
                "days": days,
                "advertisers": advertiser_data,
                "gap_analysis": gap_analysis,
            },
            indent=2,
        )

    # ── Market-wide tools ──────────────────────────────────────────────

    @server.tool(
        name="get_market_top_apps",
        description=(
            "Get market-wide app rankings from SensorTower. "
            "Returns top apps by downloads, revenue, DAU, or impression share. "
            "Use this for questions like 'which apps have the most downloads?' or 'top Finance apps by DAU'."
        ),
    )
    def get_market_top_apps(
        category: str = "Finance",
        country: str = "US",
        sort_by: str = "downloads",
        limit: int = 20,
        scrape_month: str | None = None,
    ) -> str:
        from adintel.db.models import SensorTowerMarketTopAppRecord
        from adintel.platforms.sensortower_parsers import CATEGORY_NAMES

        valid_sort = {"downloads", "revenue", "dau", "impression_share", "rank"}
        if sort_by not in valid_sort:
            return json.dumps({"error": f"Invalid sort_by. Options: {', '.join(sorted(valid_sort))}"})

        # Resolve category name to ID or use as-is
        reverse = {v.lower(): k for k, v in CATEGORY_NAMES.items()}
        category_name = category
        if category.lower() in reverse:
            category_name = CATEGORY_NAMES[reverse[category.lower()]]
        elif category in CATEGORY_NAMES:
            category_name = CATEGORY_NAMES[category]

        limit = max(1, min(limit, 100))

        with _session_factory()() as session:
            q = select(SensorTowerMarketTopAppRecord).where(
                SensorTowerMarketTopAppRecord.country == country,
            )

            # Match category flexibly (exact or case-insensitive)
            q = q.where(
                SensorTowerMarketTopAppRecord.category.ilike(category_name)
            )

            if scrape_month:
                q = q.where(SensorTowerMarketTopAppRecord.scrape_month == date.fromisoformat(scrape_month))
            else:
                # Latest available month
                latest = session.scalar(
                    select(func.max(SensorTowerMarketTopAppRecord.scrape_month))
                    .where(SensorTowerMarketTopAppRecord.country == country)
                    .where(SensorTowerMarketTopAppRecord.category.ilike(category_name))
                )
                if latest is None:
                    return json.dumps({
                        "error": f"No market data found for category='{category}', country='{country}'.",
                        "hint": "Run 'adintel collect market-top-apps' to collect market data first.",
                    })
                q = q.where(SensorTowerMarketTopAppRecord.scrape_month == latest)

            # Sort
            sort_col = getattr(SensorTowerMarketTopAppRecord, sort_by)
            if sort_by == "rank":
                q = q.order_by(sort_col.asc())
            else:
                q = q.order_by(sort_col.desc().nulls_last())

            rows = session.scalars(q.limit(limit)).all()

        results = []
        for row in rows:
            results.append({
                "rank": row.rank,
                "app_name": row.app_name,
                "publisher_name": row.publisher_name,
                "unified_app_id": row.unified_app_id,
                "primary_category": row.primary_category,
                "downloads": row.downloads,
                "revenue": _to_float(row.revenue),
                "dau": row.dau,
                "impression_share": _to_float(row.impression_share),
                "ad_on_admob": row.ad_on_admob,
                "ad_on_facebook": row.ad_on_facebook,
                "ad_on_tiktok": row.ad_on_tiktok,
                "ad_on_youtube": row.ad_on_youtube,
                "ad_on_applovin": row.ad_on_applovin,
                "ad_on_unity": row.ad_on_unity,
                "scrape_month": row.scrape_month.isoformat(),
                "country": row.country,
            })

        return json.dumps({
            "category": category_name,
            "country": country,
            "sort_by": sort_by,
            "count": len(results),
            "data": results,
        }, indent=2)

    # ── GEO (Generative Engine Optimization) Analysis Tools ─────────────

    def _resolve_geo_target(session, advertiser_name: str) -> str:
        """Resolve a display name like 'Chime' to its otterly domain like 'chime.com'.

        Resolution order:
        1. Exact match in otterlyai_prompts (already a domain key).
        2. Advertiser catalog domain match (display name → domain).
        3. Case-insensitive partial name match in catalog.
        4. Fall back to the original string (let callers handle missing data).
        """
        from sqlalchemy import func as _func
        from adintel.db.models import AdvertiserRecord

        # 1. Exact match already in otterly data
        exists = session.scalar(
            select(OtterlyPromptRecord.target_brand_or_domain_name)
            .where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
            .limit(1)
        )
        if exists:
            return advertiser_name

        # 2 & 3. Look up via advertiser catalog
        row = session.scalar(
            select(AdvertiserRecord).where(
                _func.lower(AdvertiserRecord.name) == advertiser_name.lower()
            )
        )
        if row is None:
            row = session.scalar(
                select(AdvertiserRecord).where(
                    _func.lower(AdvertiserRecord.name).contains(advertiser_name.lower())
                )
            )
        if row is not None and row.domain:
            # Confirm the domain exists in otterly data
            domain_exists = session.scalar(
                select(OtterlyPromptRecord.target_brand_or_domain_name)
                .where(OtterlyPromptRecord.target_brand_or_domain_name == row.domain)
                .limit(1)
            )
            if domain_exists:
                return row.domain

        # 4. Fuzzy domain match directly in otterly data (e.g. "Dave" → "dave.com")
        fuzzy = session.scalar(
            select(OtterlyPromptRecord.target_brand_or_domain_name)
            .where(OtterlyPromptRecord.target_brand_or_domain_name.ilike(f"%{advertiser_name}%"))
            .limit(1)
        )
        if fuzzy:
            return fuzzy

        return advertiser_name

    def _geo_engine_breakdown(session, target: str, country: str | None) -> list[dict]:
        q = (
            select(
                OtterlyPromptRecord.ai_engine,
                func.count().label("total_prompts"),
                func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited_prompts"),
                func.avg(OtterlyPromptRecord.sentiment_score).label("avg_sentiment"),
                func.avg(OtterlyPromptRecord.target_rank).label("avg_rank"),
            )
            .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
            .group_by(OtterlyPromptRecord.ai_engine)
        )
        if country:
            q = q.where(OtterlyPromptRecord.country_code == country.lower())
        rows = session.execute(q).all()
        result = []
        for row in rows:
            total = row.total_prompts or 0
            cited = row.cited_prompts or 0
            result.append({
                "ai_engine": row.ai_engine,
                "total_prompts": total,
                "cited_prompts": cited,
                "visibility_rate": round(cited / total, 4) if total > 0 else 0,
                "avg_sentiment": round(float(row.avg_sentiment), 2) if row.avg_sentiment is not None else None,
                "avg_rank": round(float(row.avg_rank), 1) if row.avg_rank is not None else None,
            })
        return sorted(result, key=lambda x: x["total_prompts"], reverse=True)

    def _geo_sentiment_distribution(session, target: str, country: str | None) -> dict:
        q = (
            select(
                OtterlyPromptRecord.sentiment_label,
                func.count().label("cnt"),
            )
            .where(OtterlyPromptRecord.target_brand_or_domain_name == target)
            .where(OtterlyPromptRecord.sentiment_label.isnot(None))
            .group_by(OtterlyPromptRecord.sentiment_label)
        )
        if country:
            q = q.where(OtterlyPromptRecord.country_code == country.lower())
        rows = session.execute(q).all()
        return {row.sentiment_label: row.cnt for row in rows}

    def _geo_top_cited_domains(session, target: str, country: str | None, limit: int = 15) -> list[dict]:
        q = (
            select(
                OtterlyCitationRecord.cited_domain,
                func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                func.count().label("appearances"),
                func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentions"),
            )
            .where(OtterlyCitationRecord.target_brand_or_domain_name == target)
            .where(OtterlyCitationRecord.cited_domain.isnot(None))
            .group_by(OtterlyCitationRecord.cited_domain)
            .order_by(func.sum(OtterlyCitationRecord.citation_count).desc())
            .limit(limit)
        )
        if country:
            q = q.where(OtterlyCitationRecord.country_code == country.lower())
        rows = session.execute(q).all()
        return [
            {
                "domain": row.cited_domain,
                "total_citations": row.total_citations or 0,
                "appearances": row.appearances,
                "brand_mentioned_count": row.brand_mentions or 0,
            }
            for row in rows
        ]

    @server.tool(
        name="get_geo_visibility_summary",
        description=(
            "Single-advertiser GEO (Generative Engine Optimization) overview. "
            "Shows how visible this brand is across AI engines (ChatGPT, Perplexity, Gemini, etc.), "
            "including visibility rate, sentiment, ranking, and top cited domains. "
            "Use for understanding a brand's AI search presence."
        ),
    )
    def get_geo_visibility_summary(
        advertiser_name: str,
        country: str | None = None,
    ) -> str:
        with _session_factory()() as session:
            advertiser_name = _resolve_geo_target(session, advertiser_name)
            # Aggregate overview via SQL instead of loading all rows
            overview_q = (
                select(
                    func.count().label("total"),
                    func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                    func.min(OtterlyPromptRecord.query_window_start_date).label("earliest"),
                    func.max(OtterlyPromptRecord.query_window_end_date).label("latest"),
                )
                .where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
            )
            if country:
                overview_q = overview_q.where(OtterlyPromptRecord.country_code == country.lower())

            stats = session.execute(overview_q).one()
            total = stats.total or 0
            if total == 0:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No Otterly GEO data found for this advertiser.",
                })

            cited = stats.cited or 0
            engine_breakdown = _geo_engine_breakdown(session, advertiser_name, country)
            sentiment_dist = _geo_sentiment_distribution(session, advertiser_name, country)
            top_domains = _geo_top_cited_domains(session, advertiser_name, country)

        return json.dumps({
            "advertiser_name": advertiser_name,
            "country": country,
            "date_range": {
                "earliest": stats.earliest.isoformat() if stats.earliest else None,
                "latest": stats.latest.isoformat() if stats.latest else None,
            },
            "overview": {
                "total_prompts_tracked": total,
                "prompts_where_cited": cited,
                "visibility_rate": round(cited / total, 4) if total > 0 else 0,
            },
            "engine_breakdown": engine_breakdown,
            "sentiment_distribution": sentiment_dist,
            "top_cited_domains": top_domains,
        }, indent=2)

    @server.tool(
        name="compare_geo_visibility",
        description=(
            "Side-by-side GEO visibility comparison for 2+ brands. "
            "Shows who is winning in AI search, engine-by-engine gap analysis, "
            "sentiment comparison, competitor overlap, and blind spots. "
            "Use for competitive GEO analysis."
        ),
    )
    def compare_geo_visibility(
        advertiser_names: str,
        country: str | None = None,
    ) -> str:
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if len(names) < 2:
            return json.dumps({"error": "Provide at least 2 comma-separated advertiser names."})

        with _session_factory()() as _resolve_session:
            names = [_resolve_geo_target(_resolve_session, n) for n in names]

        advertiser_data: dict[str, dict] = {}

        with _session_factory()() as session:
            for name in names:
                # Aggregate counts via SQL
                overview_q = (
                    select(
                        func.count().label("total"),
                        func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                    )
                    .where(OtterlyPromptRecord.target_brand_or_domain_name == name)
                )
                if country:
                    overview_q = overview_q.where(OtterlyPromptRecord.country_code == country.lower())

                stats = session.execute(overview_q).one()
                total = stats.total or 0
                if total == 0:
                    advertiser_data[name] = {"found": False}
                    continue

                cited = stats.cited or 0
                engines = _geo_engine_breakdown(session, name, country)
                sentiment = _geo_sentiment_distribution(session, name, country)

                # Load only the competitors JSON column for aggregation
                comp_q = (
                    select(OtterlyPromptRecord.competitors)
                    .where(OtterlyPromptRecord.target_brand_or_domain_name == name)
                    .where(OtterlyPromptRecord.competitors.isnot(None))
                )
                if country:
                    comp_q = comp_q.where(OtterlyPromptRecord.country_code == country.lower())
                comp_rows = session.scalars(comp_q).all()

                all_competitors: dict[str, int] = {}
                for comp_list in comp_rows:
                    for comp in (comp_list or []):
                        all_competitors[comp] = all_competitors.get(comp, 0) + 1

                advertiser_data[name] = {
                    "found": True,
                    "total_prompts": total,
                    "cited_prompts": cited,
                    "visibility_rate": round(cited / total, 4) if total > 0 else 0,
                    "engine_breakdown": engines,
                    "sentiment_distribution": sentiment,
                    "top_competitors_mentioned": dict(
                        sorted(all_competitors.items(), key=lambda x: x[1], reverse=True)[:10]
                    ),
                }

            # Gap analysis across advertisers
            found = {n: d for n, d in advertiser_data.items() if d.get("found")}
            gap_analysis: dict = {}

            if len(found) >= 2:
                # Engine coverage: which engines cite each brand
                engine_sets: dict[str, set] = {}
                for n, d in found.items():
                    engine_sets[n] = {
                        e["ai_engine"] for e in d["engine_breakdown"] if e["cited_prompts"] > 0
                    }

                all_engines = set().union(*engine_sets.values())
                blind_spots: dict[str, list] = {}
                for n in found:
                    others_engines = set().union(*(engine_sets[o] for o in found if o != n))
                    missing = sorted(others_engines - engine_sets[n])
                    if missing:
                        blind_spots[n] = missing

                # Visibility ranking
                vis_ranking = sorted(
                    [(n, d["visibility_rate"]) for n, d in found.items()],
                    key=lambda x: x[1],
                    reverse=True,
                )

                # Competitor overlap
                competitor_sets = {
                    n: set(d.get("top_competitors_mentioned", {}).keys())
                    for n, d in found.items()
                }
                shared_competitors = sorted(set.intersection(*competitor_sets.values())) if competitor_sets else []

                opportunities: list[str] = []
                leader_name, leader_rate = vis_ranking[0]
                for n, rate in vis_ranking[1:]:
                    gap = leader_rate - rate
                    if gap > 0:
                        opportunities.append(
                            f"{n} trails {leader_name} by {gap:.1%} visibility rate"
                        )
                for n, engines in blind_spots.items():
                    for eng in engines:
                        opportunities.append(
                            f"{n} has zero citations on {eng} — competitors are active there"
                        )

                gap_analysis = {
                    "visibility_ranking": [{"name": n, "rate": r} for n, r in vis_ranking],
                    "engine_blind_spots": blind_spots,
                    "shared_competitors": shared_competitors,
                    "opportunities": opportunities,
                }

        return json.dumps({
            "country": country,
            "advertisers": advertiser_data,
            "gap_analysis": gap_analysis,
        }, indent=2)

    @server.tool(
        name="get_geo_citation_analysis",
        description=(
            "Deep dive into which URLs and domains are getting cited in AI responses for a brand. "
            "Shows top cited domains, brand-owned vs third-party split, domain categories, "
            "and per-engine citation patterns. Use for citation strategy and content gap analysis."
        ),
    )
    def get_geo_citation_analysis(
        advertiser_name: str,
        country: str | None = None,
        limit: int = 20,
    ) -> str:
        limit = max(1, min(limit, 100))
        with _session_factory()() as session:
            advertiser_name = _resolve_geo_target(session, advertiser_name)
            # Aggregate overview via SQL
            overview_q = (
                select(
                    func.count().label("total"),
                    func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentioned"),
                )
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
            )
            if country:
                overview_q = overview_q.where(OtterlyCitationRecord.country_code == country.lower())

            stats = session.execute(overview_q).one()
            total = stats.total or 0
            if total == 0:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No Otterly citation data found for this advertiser.",
                })

            brand_mentioned_count = stats.brand_mentioned or 0

            # Top domains
            top_domains = _geo_top_cited_domains(session, advertiser_name, country, limit=limit)

            # Per-engine citation counts
            engine_q = (
                select(
                    OtterlyCitationRecord.ai_engine,
                    func.count().label("citation_rows"),
                    func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                    func.sum(case((OtterlyCitationRecord.brand_mentioned.is_(True), 1), else_=0)).label("brand_mentions"),
                )
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                .group_by(OtterlyCitationRecord.ai_engine)
            )
            if country:
                engine_q = engine_q.where(OtterlyCitationRecord.country_code == country.lower())
            engine_rows = session.execute(engine_q).all()
            engine_breakdown = [
                {
                    "ai_engine": row.ai_engine,
                    "citation_rows": row.citation_rows,
                    "total_citations": row.total_citations or 0,
                    "brand_mentioned_count": row.brand_mentions or 0,
                }
                for row in sorted(engine_rows, key=lambda r: r.total_citations or 0, reverse=True)
            ]

            # Domain category distribution
            cat_q = (
                select(
                    OtterlyCitationRecord.domain_category,
                    func.count().label("cnt"),
                    func.sum(OtterlyCitationRecord.citation_count).label("total_citations"),
                )
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                .where(OtterlyCitationRecord.domain_category.isnot(None))
                .group_by(OtterlyCitationRecord.domain_category)
                .order_by(func.sum(OtterlyCitationRecord.citation_count).desc())
                .limit(15)
            )
            if country:
                cat_q = cat_q.where(OtterlyCitationRecord.country_code == country.lower())
            cat_rows = session.execute(cat_q).all()
            category_distribution = [
                {"category": row.domain_category, "count": row.cnt, "total_citations": row.total_citations or 0}
                for row in cat_rows
            ]

            # Load only competitors JSON column for aggregation
            comp_q = (
                select(OtterlyCitationRecord.competitors)
                .where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                .where(OtterlyCitationRecord.competitors.isnot(None))
            )
            if country:
                comp_q = comp_q.where(OtterlyCitationRecord.country_code == country.lower())
            comp_rows = session.scalars(comp_q).all()

            all_competitors: dict[str, int] = {}
            for comp_list in comp_rows:
                for comp in (comp_list or []):
                    all_competitors[comp] = all_competitors.get(comp, 0) + 1

        return json.dumps({
            "advertiser_name": advertiser_name,
            "country": country,
            "overview": {
                "total_citation_rows": total,
                "brand_mentioned_count": brand_mentioned_count,
                "brand_mention_rate": round(brand_mentioned_count / total, 4) if total > 0 else 0,
            },
            "top_cited_domains": top_domains,
            "engine_breakdown": engine_breakdown,
            "category_distribution": category_distribution,
            "competitors_in_citations": dict(
                sorted(all_competitors.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
        }, indent=2)

    @server.tool(
        name="get_geo_prompt_insights",
        description=(
            "Analyze the prompts/queries where a brand appears (or doesn't) in AI search. "
            "Shows top prompts by volume, prompts where brand ranks well vs poorly, "
            "blind-spot prompts where competitors appear but the brand doesn't, "
            "and sentiment distribution. Use for GEO content strategy."
        ),
    )
    def get_geo_prompt_insights(
        advertiser_name: str,
        country: str | None = None,
        limit: int = 20,
    ) -> str:
        limit = max(1, min(limit, 100))
        with _session_factory()() as session:
            advertiser_name = _resolve_geo_target(session, advertiser_name)
            base_q = select(OtterlyPromptRecord).where(
                OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name
            )
            if country:
                base_q = base_q.where(OtterlyPromptRecord.country_code == country.lower())

            all_prompts = session.scalars(base_q).all()
            if not all_prompts:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No Otterly prompt data found for this advertiser.",
                })

            # Top prompts by volume
            with_volume = [p for p in all_prompts if p.prompt_volume is not None]
            top_by_volume = sorted(with_volume, key=lambda p: p.prompt_volume or 0, reverse=True)[:limit]
            top_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "rank": p.target_rank,
                    "cited": p.domain_cited,
                    "ai_engine": p.ai_engine,
                    "sentiment": p.sentiment_label,
                }
                for p in top_by_volume
            ]

            # Best ranked prompts (lowest rank = best)
            with_rank = [p for p in all_prompts if p.target_rank is not None and p.target_rank > 0]
            best_ranked = sorted(with_rank, key=lambda p: p.target_rank)[:limit]
            best_ranked_prompts = [
                {
                    "prompt": p.prompt_text,
                    "rank": p.target_rank,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "cited": p.domain_cited,
                }
                for p in best_ranked
            ]

            # Blind spots: high-volume prompts where brand is NOT cited but competitors are present
            blind_spots = [
                p for p in all_prompts
                if not p.domain_cited and (p.competitors or [])
            ]
            blind_spots_sorted = sorted(blind_spots, key=lambda p: p.prompt_volume or 0, reverse=True)[:limit]
            blind_spot_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "competitors_present": p.competitors,
                    "sentiment": p.sentiment_label,
                }
                for p in blind_spots_sorted
            ]

            # Negative sentiment prompts
            negative = [p for p in all_prompts if p.sentiment_label == "negative"]
            negative_sorted = sorted(negative, key=lambda p: p.prompt_volume or 0, reverse=True)[:10]
            negative_prompts = [
                {
                    "prompt": p.prompt_text,
                    "volume": p.prompt_volume,
                    "ai_engine": p.ai_engine,
                    "sentiment_score": _to_float(p.sentiment_score),
                }
                for p in negative_sorted
            ]

            sentiment_dist = _geo_sentiment_distribution(session, advertiser_name, country)

        return json.dumps({
            "advertiser_name": advertiser_name,
            "country": country,
            "total_prompts": len(all_prompts),
            "top_prompts_by_volume": top_prompts,
            "best_ranked_prompts": best_ranked_prompts,
            "blind_spot_prompts": blind_spot_prompts,
            "negative_sentiment_prompts": negative_prompts,
            "sentiment_distribution": sentiment_dist,
        }, indent=2)

    @server.tool(
        name="get_geo_data_availability",
        description=(
            "Check what GEO (Otterly) data is available in the database. "
            "Shows which brands have data, which AI engines are covered, date range, "
            "row counts, and field completeness gaps. Use this before running any GEO "
            "analysis to understand data coverage."
        ),
    )
    def get_geo_data_availability(advertiser_name: str | None = None) -> str:
        with _session_factory()() as session:
            if advertiser_name:
                advertiser_name = _resolve_geo_target(session, advertiser_name)

            def _prompt_filter(q):
                if advertiser_name:
                    q = q.where(OtterlyPromptRecord.target_brand_or_domain_name == advertiser_name)
                return q

            def _citation_filter(q):
                if advertiser_name:
                    q = q.where(OtterlyCitationRecord.target_brand_or_domain_name == advertiser_name)
                return q

            # Overall prompt stats
            p_total = session.scalar(_prompt_filter(select(func.count()).select_from(OtterlyPromptRecord))) or 0
            c_total = session.scalar(_citation_filter(select(func.count()).select_from(OtterlyCitationRecord))) or 0

            if p_total == 0 and c_total == 0:
                return json.dumps({
                    "found": False,
                    "advertiser_name": advertiser_name,
                    "message": "No GEO data found." + (f" '{advertiser_name}' is not tracked in Otterly." if advertiser_name else ""),
                })

            # Date range
            date_q = _prompt_filter(
                select(
                    func.min(OtterlyPromptRecord.query_window_start_date).label("earliest"),
                    func.max(OtterlyPromptRecord.query_window_end_date).label("latest"),
                    func.count(OtterlyPromptRecord.query_window_end_date.distinct()).label("snapshots"),
                ).select_from(OtterlyPromptRecord)
            )
            dates = session.execute(date_q).one()

            # Per-brand prompt coverage
            brand_q = _prompt_filter(
                select(
                    OtterlyPromptRecord.target_brand_or_domain_name,
                    OtterlyPromptRecord.ai_engine,
                    func.count().label("prompt_rows"),
                    func.sum(case((OtterlyPromptRecord.domain_cited.is_(True), 1), else_=0)).label("cited"),
                ).select_from(OtterlyPromptRecord)
                .group_by(OtterlyPromptRecord.target_brand_or_domain_name, OtterlyPromptRecord.ai_engine)
                .order_by(OtterlyPromptRecord.target_brand_or_domain_name, OtterlyPromptRecord.ai_engine)
            )
            brand_rows = session.execute(brand_q).all()

            # Aggregate by brand
            brands_summary: dict[str, dict] = {}
            for row in brand_rows:
                b = row.target_brand_or_domain_name
                if b not in brands_summary:
                    brands_summary[b] = {"engines": [], "total_prompts": 0, "total_cited": 0}
                rate = round(row.cited / row.prompt_rows, 3) if row.prompt_rows else 0
                brands_summary[b]["engines"].append({
                    "engine": row.ai_engine,
                    "prompts": row.prompt_rows,
                    "cited": row.cited,
                    "visibility_rate": rate,
                })
                brands_summary[b]["total_prompts"] += row.prompt_rows
                brands_summary[b]["total_cited"] += row.cited

            # Citation coverage per brand
            cit_q = _citation_filter(
                select(
                    OtterlyCitationRecord.target_brand_or_domain_name,
                    func.count().label("citation_rows"),
                ).select_from(OtterlyCitationRecord)
                .group_by(OtterlyCitationRecord.target_brand_or_domain_name)
            )
            for row in session.execute(cit_q).all():
                if row.target_brand_or_domain_name in brands_summary:
                    brands_summary[row.target_brand_or_domain_name]["citation_rows"] = row.citation_rows

            # Field completeness gaps (on full table or filtered)
            null_sentiment = session.scalar(
                _prompt_filter(select(func.count()).select_from(OtterlyPromptRecord).where(OtterlyPromptRecord.sentiment_label.is_(None)))
            ) or 0
            gap_pct = round(null_sentiment / p_total, 3) if p_total else 0
            field_gaps = []
            if gap_pct > 0.05:
                field_gaps.append({
                    "field": "sentiment_label",
                    "null_rows": null_sentiment,
                    "null_pct": gap_pct,
                    "impact": "sentiment analysis and negative-prompt detection will be incomplete",
                })

            # Engines present
            engines = [r[0] for r in session.execute(
                _prompt_filter(select(OtterlyPromptRecord.ai_engine).distinct().select_from(OtterlyPromptRecord))
            ).all()]
            countries = [r[0] for r in session.execute(
                _prompt_filter(select(OtterlyPromptRecord.country_code).distinct().select_from(OtterlyPromptRecord))
            ).all()]

        return json.dumps({
            "scope": advertiser_name or "all brands",
            "summary": {
                "brands_tracked": len(brands_summary),
                "ai_engines": sorted(engines),
                "countries": sorted(countries),
                "date_range": {
                    "start": dates.earliest.isoformat() if dates.earliest else None,
                    "end": dates.latest.isoformat() if dates.latest else None,
                    "snapshots": dates.snapshots,
                },
                "total_prompt_rows": p_total,
                "total_citation_rows": c_total,
            },
            "brands": {
                b: {
                    **d,
                    "visibility_rate": round(d["total_cited"] / d["total_prompts"], 3) if d["total_prompts"] else 0,
                }
                for b, d in brands_summary.items()
            },
            "field_gaps": field_gaps,
            "ready_for_analysis": len(field_gaps) == 0,
        }, indent=2)

    return server


def serve_stdio() -> None:
    create_mcp_server().run("stdio")


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
