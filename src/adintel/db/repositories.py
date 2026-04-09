from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from adintel.core.models import AdvertiserProfile
from adintel.db.models import (
    AdvertiserRecord,
    OtterlyCitationRecord,
    OtterlyPromptRecord,
    RequestedAdvertiserRecord,
    ScrapeRunMetricRecord,
    SensorTowerAsoKeywordRecord,
    SensorTowerCreativeRecord,
    ScrapeRunRecord,
    SensorTowerDemographicRecord,
    SensorTowerDownloadRecord,
    SensorTowerImpressionShareRecord,
    SensorTowerRankingRecord,
    SensorTowerReviewRecord,
    SensorTowerReviewTextRecord,
    SensorTowerMarketTopAppRecord,
    SensorTowerRetentionRecord,
    SensorTowerUsageRecord,
)


def _to_profile(record: AdvertiserRecord) -> AdvertiserProfile:
    return AdvertiserProfile.model_validate(
        {
            "name": record.name,
            "domain": record.domain,
            "category": record.category,
            "countries": [c for c in record.countries_csv.split(",") if c],
            "platforms": {
                "adclarity": {
                    "advertiser_id": record.adclarity_advertiser_id,
                    "brand_id": record.adclarity_brand_id,
                },
                "sensortower": {
                    "unified_app_id": record.sensortower_unified_app_id,
                    "publisher_id": record.sensortower_publisher_id,
                    "ios_app_id": record.sensortower_ios_app_id,
                    "android_package": record.sensortower_android_package,
                },
            },
        }
    )


class AdvertiserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self) -> list[AdvertiserProfile]:
        rows = self.session.scalars(select(AdvertiserRecord).order_by(AdvertiserRecord.name)).all()
        return [_to_profile(row) for row in rows]

    def get(self, name: str) -> AdvertiserProfile | None:
        row = self.session.scalar(select(AdvertiserRecord).where(AdvertiserRecord.name == name))
        if row is None:
            return None
        return _to_profile(row)

    def resolve(self, query: str) -> AdvertiserProfile | None:
        """Fuzzy lookup: exact → case-insensitive → substring → single high-confidence fuzzy match.

        When fuzzy auto-resolution is used (step 4), the returned profile will have
        ``_resolved_from`` set to the original query string so callers can surface it.
        """
        # 1. Exact match
        row = self.session.scalar(
            select(AdvertiserRecord).where(AdvertiserRecord.name == query)
        )
        if row:
            return _to_profile(row)

        q = query.lower().strip()

        # 2. Case-insensitive name or domain exact match
        row = self.session.scalar(
            select(AdvertiserRecord).where(
                or_(
                    AdvertiserRecord.name.ilike(q),
                    AdvertiserRecord.domain.ilike(q),
                )
            )
        )
        if row:
            profile = _to_profile(row)
            if query != row.name:
                profile._resolved_from = query  # type: ignore[attr-defined]
            return profile

        # 3. Substring match on name or domain (e.g. "pokemon go" ⊂ "pokemongolive.com")
        slug = q.replace(" ", "").replace("-", "").replace("_", "")
        rows = self.session.scalars(select(AdvertiserRecord)).all()
        for r in rows:
            name_slug = (r.name or "").lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
            domain_slug = (r.domain or "").lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
            name_matches = slug in name_slug or (name_slug and name_slug in slug)
            domain_matches = slug in domain_slug or (domain_slug and domain_slug in slug)
            if name_matches or domain_matches:
                return _to_profile(r)

        # 4. Single high-confidence fuzzy match (typo correction)
        candidates = self.suggest(query, limit=2, threshold=0.7)
        if len(candidates) == 1:
            row = self.session.scalar(
                select(AdvertiserRecord).where(AdvertiserRecord.name == candidates[0])
            )
            if row:
                profile = _to_profile(row)
                profile._resolved_from = query  # type: ignore[attr-defined]
                return profile

        return None

    def suggest(self, query: str, limit: int = 3, threshold: float = 0.5) -> list[str]:
        """Return up to `limit` candidate advertiser names that fuzzy-match `query`.

        Uses SequenceMatcher on both name and domain so typos like 'pokmon go'
        still surface 'pokemongolive.com'. Only candidates above `threshold`
        similarity are returned, sorted best-first.
        """
        from difflib import SequenceMatcher

        q = query.lower().strip()
        rows = self.session.scalars(select(AdvertiserRecord)).all()
        scored: list[tuple[float, str]] = []
        for r in rows:
            name_score = SequenceMatcher(None, q, (r.name or "").lower()).ratio()
            domain_score = SequenceMatcher(None, q, (r.domain or "").lower()).ratio()
            best = max(name_score, domain_score)
            if best >= threshold:
                scored.append((best, r.name))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _, name in scored[:limit]]

    def upsert(self, advertiser: AdvertiserProfile) -> AdvertiserProfile:
        record = self.session.scalar(
            select(AdvertiserRecord).where(AdvertiserRecord.name == advertiser.name)
        )
        if record is None:
            record = AdvertiserRecord(name=advertiser.name)
            self.session.add(record)

        record.domain = advertiser.domain
        record.category = advertiser.category
        record.countries_csv = ",".join(advertiser.countries or ["US"])
        record.adclarity_advertiser_id = advertiser.platforms.adclarity.advertiser_id
        record.adclarity_brand_id = advertiser.platforms.adclarity.brand_id
        record.sensortower_unified_app_id = advertiser.platforms.sensortower.unified_app_id
        record.sensortower_publisher_id = advertiser.platforms.sensortower.publisher_id
        record.sensortower_ios_app_id = advertiser.platforms.sensortower.ios_app_id
        record.sensortower_android_package = advertiser.platforms.sensortower.android_package

        self.session.commit()
        self.session.refresh(record)
        return _to_profile(record)

    def sync(self, advertisers: list[AdvertiserProfile]) -> int:
        count = 0
        for advertiser in advertisers:
            self.upsert(advertiser)
            count += 1
        return count


class ScrapeRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, advertiser_name: str, platform: str) -> ScrapeRunRecord:
        run = ScrapeRunRecord(advertiser_name=advertiser_name, platform=platform)
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def finish(
        self,
        run: ScrapeRunRecord,
        *,
        status: str,
        message: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        run.status = status
        run.message = message
        run.result_metadata = metadata
        run.finished_at = datetime.now(UTC)
        self.session.add(run)
        self.session.commit()

    def list_recent(
        self,
        *,
        advertiser_name: str | None = None,
        platform: str | None = None,
        limit: int = 20,
    ) -> list[ScrapeRunRecord]:
        query = select(ScrapeRunRecord)
        if advertiser_name:
            query = query.where(ScrapeRunRecord.advertiser_name == advertiser_name)
        if platform:
            query = query.where(ScrapeRunRecord.platform == platform)
        return self.session.scalars(
            query.order_by(ScrapeRunRecord.started_at.desc()).limit(limit)
        ).all()


class ScrapeRunMetricRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, scrape_run_id: int, metric_name: str) -> ScrapeRunMetricRecord:
        record = ScrapeRunMetricRecord(
            scrape_run_id=scrape_run_id,
            metric_name=metric_name,
            status="running",
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def finish(
        self,
        record: ScrapeRunMetricRecord,
        *,
        status: str,
        records_written: int = 0,
        message: str | None = None,
    ) -> None:
        record.status = status
        record.records_written = records_written
        record.message = message
        record.finished_at = datetime.now(UTC)
        self.session.add(record)
        self.session.commit()


class CollectionHealthRepository:
    """Read-only queries for collection health and staleness."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_health(self, advertiser_name: str, platform: str) -> dict:
        """Return last success time, consecutive failures, and staleness info."""
        runs = self.session.scalars(
            select(ScrapeRunRecord)
            .where(
                ScrapeRunRecord.advertiser_name == advertiser_name,
                ScrapeRunRecord.platform == platform,
            )
            .order_by(ScrapeRunRecord.started_at.desc())
            .limit(20)
        ).all()

        if not runs:
            return {
                "advertiser_name": advertiser_name,
                "platform": platform,
                "last_success_at": None,
                "consecutive_failures": 0,
                "hours_since_success": None,
                "last_error_message": None,
                "total_runs": 0,
            }

        consecutive_failures = 0
        last_success_at = None
        last_error_message = None

        for run in runs:
            if run.status == "error":
                consecutive_failures += 1
                if last_error_message is None:
                    last_error_message = run.message
            else:
                if last_success_at is None and run.status in ("success", "partial"):
                    last_success_at = run.finished_at or run.started_at
                break

        hours_since_success = None
        if last_success_at is not None:
            now = datetime.now(UTC)
            # Handle naive datetimes from DB
            success_time = last_success_at if last_success_at.tzinfo else last_success_at.replace(tzinfo=UTC)
            hours_since_success = round((now - success_time).total_seconds() / 3600, 1)

        return {
            "advertiser_name": advertiser_name,
            "platform": platform,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "consecutive_failures": consecutive_failures,
            "hours_since_success": hours_since_success,
            "last_error_message": last_error_message,
            "total_runs": len(runs),
        }

    def get_metric_health(self, scrape_run_id: int) -> list[dict]:
        """Return per-metric status for a specific run."""
        rows = self.session.scalars(
            select(ScrapeRunMetricRecord)
            .where(ScrapeRunMetricRecord.scrape_run_id == scrape_run_id)
            .order_by(ScrapeRunMetricRecord.metric_name)
        ).all()
        return [
            {
                "metric_name": row.metric_name,
                "status": row.status,
                "records_written": row.records_written,
                "message": row.message,
            }
            for row in rows
        ]

    def get_all_health(self) -> list[dict]:
        """Return health for all advertiser/platform combinations that have runs."""
        pairs = self.session.execute(
            select(ScrapeRunRecord.advertiser_name, ScrapeRunRecord.platform)
            .distinct()
        ).all()
        return [self.get_health(name, platform) for name, platform in pairs]

    def get_health_for_advertiser(self, advertiser_name: str) -> list[dict]:
        """Return health for every platform an advertiser has run on."""
        platforms = self.session.execute(
            select(ScrapeRunRecord.platform)
            .where(ScrapeRunRecord.advertiser_name == advertiser_name)
            .distinct()
        ).all()
        return [self.get_health(advertiser_name, platform) for (platform,) in platforms]

    def get_stale_advertisers(self, platform: str, stale_hours: float = 48) -> list[str]:
        """Return advertiser names whose last successful run is older than stale_hours (or never ran)."""
        pairs = self.session.execute(
            select(ScrapeRunRecord.advertiser_name)
            .where(ScrapeRunRecord.platform == platform)
            .distinct()
        ).all()

        # Also include advertisers that have never been collected
        all_advertiser_names = {
            row for row in self.session.scalars(
                select(AdvertiserRecord.name)
            ).all()
        }
        collected_names = {name for (name,) in pairs}
        never_collected = all_advertiser_names - collected_names

        stale = list(never_collected)
        for (name,) in pairs:
            health = self.get_health(name, platform)
            if health["hours_since_success"] is None or health["hours_since_success"] >= stale_hours:
                stale.append(name)

        return sorted(stale)

    def get_alerts(self, *, stale_hours: float = 48, max_consecutive_failures: int = 3) -> list[dict]:
        """Return active alerts based on staleness and failure thresholds."""
        all_health = self.get_all_health()
        alerts = []
        for health in all_health:
            if health["consecutive_failures"] >= max_consecutive_failures:
                alerts.append({
                    "advertiser_name": health["advertiser_name"],
                    "platform": health["platform"],
                    "alert_type": "consecutive_failures",
                    "severity": "critical",
                    "message": f"{health['consecutive_failures']} consecutive failures. Last error: {health['last_error_message']}",
                })
            if health["hours_since_success"] is not None and health["hours_since_success"] >= stale_hours:
                severity = "critical" if health["hours_since_success"] >= stale_hours * 3.5 else "warning"
                alerts.append({
                    "advertiser_name": health["advertiser_name"],
                    "platform": health["platform"],
                    "alert_type": "stale_data",
                    "severity": severity,
                    "message": f"Data is {health['hours_since_success']}h old (threshold: {stale_hours}h).",
                })
            if health["last_success_at"] is None and health["total_runs"] > 0:
                alerts.append({
                    "advertiser_name": health["advertiser_name"],
                    "platform": health["platform"],
                    "alert_type": "never_succeeded",
                    "severity": "critical",
                    "message": f"No successful collection in {health['total_runs']} runs.",
                })
        for platform in ("sensortower", "adclarity"):
            for advertiser_name in self.get_stale_advertisers(platform, stale_hours=stale_hours):
                if any(
                    alert["advertiser_name"] == advertiser_name
                    and alert["platform"] == platform
                    and alert["alert_type"] == "never_collected"
                    for alert in alerts
                ):
                    continue
                health = self.get_health(advertiser_name, platform)
                if health["total_runs"] == 0:
                    alerts.append({
                        "advertiser_name": advertiser_name,
                        "platform": platform,
                        "alert_type": "never_collected",
                        "severity": "warning",
                        "message": "No collection runs have been recorded yet.",
                    })
        return alerts


class RequestedAdvertiserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def request(self, name: str, requested_by: str | None = None, context: str | None = None) -> None:
        row = self.session.scalar(
            select(RequestedAdvertiserRecord).where(RequestedAdvertiserRecord.name == name)
        )
        if row is None:
            row = RequestedAdvertiserRecord(name=name)
            self.session.add(row)
        row.requested_by = requested_by or row.requested_by
        row.context = context or row.context
        self.session.commit()


def _bulk_upsert(
    session: Session,
    model,
    rows: list[dict],
    *,
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
) -> int:
    if not rows:
        return 0

    if session.bind is None or session.bind.dialect.name != "postgresql":
        raise RuntimeError("AdIntel bulk upserts currently require a PostgreSQL database.")

    # PostgreSQL cannot update the same conflict target twice within one
    # INSERT .. ON CONFLICT statement, so collapse duplicate keys first.
    deduped_rows: dict[tuple[object, ...], dict] = {}
    for row in rows:
        conflict_key = tuple(row[column] for column in conflict_columns)
        deduped_rows[conflict_key] = row
    rows = list(deduped_rows.values())

    all_columns = set().union(*(row.keys() for row in rows))
    rows = [{column: row.get(column) for column in all_columns} for row in rows]

    update_targets = update_columns or [
        key for key in rows[0].keys() if key not in set(conflict_columns)
    ]

    # PostgreSQL limits parameters to 65535; chunk to stay well under that.
    num_cols = len(rows[0])
    chunk_size = max(1, 60_000 // num_cols)
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        stmt = insert(model).values(chunk)
        update_map = {column: getattr(stmt.excluded, column) for column in update_targets}
        session.execute(stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_map))
        session.commit()
        total += len(chunk)
    return total


class SensorTowerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_downloads(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerDownloadRecord,
            rows,
            conflict_columns=["advertiser_name", "period_date", "granularity", "country", "os"],
        )

    def upsert_usage(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerUsageRecord,
            rows,
            conflict_columns=["advertiser_name", "period_date", "country"],
        )

    def upsert_retention(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerRetentionRecord,
            rows,
            conflict_columns=["advertiser_name", "cohort_date", "country"],
        )

    def upsert_impression_share(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerImpressionShareRecord,
            rows,
            conflict_columns=["advertiser_name", "period_date", "network", "country"],
        )

    def upsert_demographics(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerDemographicRecord,
            rows,
            conflict_columns=["advertiser_name", "country", "age_bracket"],
        )

    def upsert_rankings(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerRankingRecord,
            rows,
            conflict_columns=["advertiser_name", "rank_date", "country", "category", "chart_type"],
        )

    def upsert_reviews(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerReviewRecord,
            rows,
            conflict_columns=["advertiser_name", "period_date", "country"],
        )

    def upsert_review_texts(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerReviewTextRecord,
            rows,
            conflict_columns=["advertiser_name", "review_id"],
        )

    def upsert_creatives(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerCreativeRecord,
            rows,
            conflict_columns=["advertiser_name", "creative_id"],
        )

    def upsert_aso_keywords(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerAsoKeywordRecord,
            rows,
            conflict_columns=["advertiser_name", "keyword", "country", "device"],
        )

    def upsert_market_top_apps(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            SensorTowerMarketTopAppRecord,
            rows,
            conflict_columns=["scrape_month", "country", "category", "os", "rank"],
        )


class OtterlyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_prompts(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            OtterlyPromptRecord,
            rows,
            conflict_columns=[
                "target_brand_or_domain_name",
                "country_code",
                "ai_engine",
                "prompt_text",
                "query_window_end_date",
            ],
        )

    def upsert_citations(self, rows: list[dict]) -> int:
        return _bulk_upsert(
            self.session,
            OtterlyCitationRecord,
            rows,
            conflict_columns=[
                "target_brand_or_domain_name",
                "country_code",
                "ai_engine",
                "cited_url",
                "query_window_end_date",
            ],
        )
