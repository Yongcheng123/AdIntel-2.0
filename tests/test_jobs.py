from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from adintel.db.models import Base, JobRecord, ScrapeRunRecord
from adintel.db.repositories import JobRepository


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_enqueue_dedupes_active_jobs() -> None:
    session = _session()
    repo = JobRepository(session)
    job1 = repo.enqueue(advertiser_name="Chime", reason="stale")
    job2 = repo.enqueue(advertiser_name="Chime", reason="stale")
    assert job1.id == job2.id
    assert session.query(JobRecord).count() == 1


def test_enqueue_creates_new_after_terminal() -> None:
    session = _session()
    repo = JobRepository(session)
    job = repo.enqueue(advertiser_name="Chime", reason="manual")
    repo.finish(job.id, "success")
    job2 = repo.enqueue(advertiser_name="Chime", reason="manual")
    assert job.id != job2.id


def test_claim_next_marks_running_and_is_fifo() -> None:
    session = _session()
    repo = JobRepository(session)
    a = repo.enqueue(advertiser_name="A", reason="manual")
    b = repo.enqueue(advertiser_name="B", reason="manual")

    claimed_first = repo.claim_next(worker_id="w1")
    assert claimed_first is not None
    assert claimed_first.id == a.id
    assert claimed_first.status == "running"
    assert claimed_first.worker_id == "w1"

    claimed_second = repo.claim_next(worker_id="w2")
    assert claimed_second is not None
    assert claimed_second.id == b.id

    assert repo.claim_next(worker_id="w3") is None


def test_finish_records_error_and_timestamp() -> None:
    session = _session()
    repo = JobRepository(session)
    job = repo.enqueue(advertiser_name="C", reason="manual")
    repo.claim_next(worker_id="w1")
    repo.finish(job.id, "failed", error="boom")
    refreshed = repo.get(job.id)
    assert refreshed.status == "failed"
    assert refreshed.error == "boom"
    assert refreshed.finished_at is not None


def test_list_recent_filters() -> None:
    session = _session()
    repo = JobRepository(session)
    repo.enqueue(advertiser_name="A", reason="stale")
    repo.enqueue(advertiser_name="B", reason="stale")
    a_only = repo.list_recent(advertiser_name="A")
    assert len(a_only) == 1
    assert a_only[0].advertiser_name == "A"


def test_freshness_helper() -> None:
    from adintel.mcp.server import _freshness

    session = _session()
    # No runs → stale, no data
    f = _freshness(session, "Chime", stale_hours=24)
    assert f["is_stale"] is True
    assert f["has_data"] is False
    assert f["last_scraped"] is None

    # Fresh run
    now = datetime.now(UTC)
    session.add(
        ScrapeRunRecord(
            advertiser_name="Chime",
            platform="sensortower",
            status="success",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=30),
        )
    )
    session.commit()
    f = _freshness(session, "Chime", stale_hours=24)
    assert f["is_stale"] is False
    assert f["has_data"] is True
    assert f["age_hours"] < 2

    # Stale run
    old = now - timedelta(hours=72)
    session.add(
        ScrapeRunRecord(
            advertiser_name="Legacy",
            platform="sensortower",
            status="success",
            started_at=old,
            finished_at=old,
        )
    )
    session.commit()
    f = _freshness(session, "Legacy", stale_hours=24)
    assert f["is_stale"] is True
    assert f["has_data"] is True


def test_request_advertiser_preserves_context() -> None:
    """Audit fix: second call without context must not clear the prior context."""
    from adintel.db.repositories import RequestedAdvertiserRepository

    session = _session()
    repo = RequestedAdvertiserRepository(session)
    repo.request(name="NewBrand", requested_by="alice", context="interesting")
    repo.request(name="NewBrand")  # both None — must not clobber
    from adintel.db.models import RequestedAdvertiserRecord
    row = session.query(RequestedAdvertiserRecord).filter_by(name="NewBrand").one()
    assert row.requested_by == "alice"
    assert row.context == "interesting"


def test_enqueue_recovers_when_unique_constraint_race_occurs(monkeypatch) -> None:
    session = _session()
    repo = JobRepository(session)
    existing = repo.enqueue(advertiser_name="Chime", reason="stale")
    session.query(JobRecord).delete()
    session.commit()

    original_commit = session.commit
    calls = {"count": 0}

    def flaky_commit() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        original_commit()

    active_calls = {"count": 0}

    def fake_active_for(advertiser_name: str, platform: str = "sensortower"):
        active_calls["count"] += 1
        if active_calls["count"] == 1:
            return None
        return existing

    monkeypatch.setattr(session, "commit", flaky_commit)
    monkeypatch.setattr(repo, "active_for", fake_active_for)

    job = repo.enqueue(advertiser_name="Chime", reason="stale")

    assert job is existing
    assert calls["count"] == 1
