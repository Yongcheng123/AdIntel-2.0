import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from adintel.collectors.service import CollectorService
from adintel.core.settings import AppSettings
from adintel.core.models import AdvertiserProfile, PlatformName
from adintel.db.models import AdvertiserRecord, Base, ScrapeRunRecord, SensorTowerRetentionRecord
from adintel.db.models import OtterlyPromptRecord
from adintel.db.session import ensure_schema
from adintel.db.repositories import CollectionHealthRepository, ScrapeRunRepository, _bulk_upsert


class FakeSession:
    def __init__(self) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.statement = None
        self.commits = 0
        self.rollbacks = 0

    def execute(self, stmt) -> None:
        self.statement = stmt

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def build_sqlite_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_bulk_upsert_dedupes_duplicate_conflict_keys() -> None:
    session = FakeSession()
    rows = [
        {
            "advertiser_name": "Chime",
            "cohort_date": "2026-03-01",
            "country": "US",
            "d1": 0.4,
            "scraped_at": "first",
        },
        {
            "advertiser_name": "Chime",
            "cohort_date": "2026-03-01",
            "country": "US",
            "d1": 0.5,
            "scraped_at": "second",
        },
    ]

    written = _bulk_upsert(
        session,
        SensorTowerRetentionRecord,
        rows,
        conflict_columns=["advertiser_name", "cohort_date", "country"],
    )

    assert written == 1
    assert session.commits == 1
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert compiled.params["d1_m0"] == 0.5
    assert compiled.params["scraped_at_m0"] == "second"


def test_collect_one_rolls_back_before_marking_error() -> None:
    class FailingCollector:
        async def collect(self, request, *, use_cdp: bool = False):
            raise RuntimeError("boom")

    class FakeRuns:
        def __init__(self) -> None:
            self.finished = None

        def start(self, advertiser_name: str, platform: str):
            return SimpleNamespace(id=1, advertiser_name=advertiser_name, platform=platform)

        def finish(self, run, *, status: str, message: str | None = None) -> None:
            self.finished = (run.id, status, message)

    service = object.__new__(CollectorService)
    service.session = FakeSession()
    service.collectors = {PlatformName.SENSORTOWER: FailingCollector()}
    service.runs = FakeRuns()

    advertiser = AdvertiserProfile(name="Chime", countries=["US"])

    try:
        asyncio.run(service.collect_one(advertiser, PlatformName.SENSORTOWER))
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("collect_one should re-raise collector failures")

    assert service.session.rollbacks == 1
    assert service.runs.finished == (1, "error", "boom")


def test_scrape_run_finish_persists_result_metadata() -> None:
    session = build_sqlite_session()
    repo = ScrapeRunRepository(session)

    run = repo.start("Chime", "sensortower")
    repo.finish(
        run,
        status="success",
        message="Collected SensorTower core metrics.",
        metadata={"records_written": 12, "result": {"metric_results": {"downloads/US": "success"}}},
    )

    stored = session.get(ScrapeRunRecord, run.id)
    assert stored is not None
    assert stored.result_metadata == {
        "records_written": 12,
        "result": {"metric_results": {"downloads/US": "success"}},
    }


def test_get_health_for_advertiser_returns_all_platforms() -> None:
    session = build_sqlite_session()
    session.add_all(
        [
            ScrapeRunRecord(advertiser_name="Chime", platform="sensortower", status="success"),
        ]
    )
    session.commit()

    repo = CollectionHealthRepository(session)
    health = repo.get_health_for_advertiser("Chime")

    assert {(row["platform"], row["last_success_at"] is not None) for row in health} == {("sensortower", True)}


def test_get_alerts_includes_never_collected_advertisers() -> None:
    session = build_sqlite_session()
    session.add(AdvertiserRecord(name="Chime"))
    session.commit()

    repo = CollectionHealthRepository(session)
    alerts = repo.get_alerts()

    assert {
        (alert["advertiser_name"], alert["platform"], alert["alert_type"])
        for alert in alerts
    } == {
        ("Chime", "sensortower", "never_collected"),
        ("Chime", "otterlyai", "never_collected"),
    }


def test_get_alerts_skips_otterly_never_collected_when_geo_rows_exist() -> None:
    session = build_sqlite_session()
    session.add(AdvertiserRecord(name="Chime", domain="chime.com"))
    session.add(
        OtterlyPromptRecord(
            target_brand_or_domain_name="chime.com",
            country_code="us",
            query_window_start_date=date(2026, 4, 1),
            query_window_end_date=date(2026, 4, 7),
            prompt_text="best checking accounts",
            ai_engine="ChatGPT",
            domain_cited=True,
        )
    )
    session.commit()

    repo = CollectionHealthRepository(session)
    alerts = repo.get_alerts()

    assert ("Chime", "otterlyai", "never_collected") not in {
        (alert["advertiser_name"], alert["platform"], alert["alert_type"])
        for alert in alerts
    }


def test_ensure_schema_applies_sql_when_hash_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "adintel.db"
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text(
        """
        CREATE TABLE IF NOT EXISTS demo_table (
          id INTEGER PRIMARY KEY,
          name TEXT
        );
        """,
        encoding="utf-8",
    )
    settings = AppSettings(
        database_url=f"sqlite+pysqlite:///{db_path}",
        state_dir=tmp_path / "state",
        auto_apply_schema=True,
    )
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    (settings.state_dir.parent / "sql").mkdir(parents=True, exist_ok=True)
    (settings.state_dir.parent / "sql" / "schema.sql").write_text(schema_path.read_text(encoding="utf-8"), encoding="utf-8")

    engine = create_engine(settings.database_url, future=True)
    ensure_schema(engine, settings)

    with engine.begin() as connection:
        exists = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'demo_table'"
        ).scalar()
        applied_hash = connection.exec_driver_sql(
            "SELECT schema_hash FROM adintel_schema_state"
        ).scalar()

    assert exists == "demo_table"
    assert applied_hash is not None
