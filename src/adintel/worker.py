"""Remote-desktop worker that drains the jobs queue by running the existing
Playwright scraper. One process claims one job at a time; run multiple
processes for concurrency (the queue uses SELECT ... FOR UPDATE SKIP LOCKED).
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid

from sqlalchemy import text

from adintel.collectors.service import CollectorService
from adintel.core.catalog import get_catalog_advertiser, load_catalog
from adintel.core.models import PlatformName
from adintel.core.settings import AppSettings, get_settings
from adintel.db.repositories import AdvertiserRepository, JobRepository
from adintel.db.session import build_session_factory

logger = logging.getLogger("adintel.worker")


def _make_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


async def _run_job(
    settings: AppSettings,
    session_factory,
    job_id: int,
    *,
    use_cdp: bool,
    headless: bool,
) -> None:
    """Execute a single claimed job. Each job uses its own session to avoid
    cross-contaminating transaction state with queue operations.
    """
    catalog = load_catalog(settings.config_file)

    with session_factory() as session:
        job_repo = JobRepository(session)
        job = job_repo.get(job_id)
        if job is None:
            logger.warning("Job %s disappeared before execution", job_id)
            return

        advertiser = get_catalog_advertiser(catalog, job.advertiser_name) or AdvertiserRepository(
            session
        ).get(job.advertiser_name)
        if advertiser is None:
            job_repo.finish(
                job.id, "failed", error=f"Advertiser '{job.advertiser_name}' not in catalog/DB"
            )
            return

        countries = (
            [c.strip().upper() for c in job.countries_csv.split(",") if c.strip()]
            if job.countries_csv
            else None
        )
        metrics = (
            [m.strip().lower() for m in job.metrics_csv.split(",") if m.strip()]
            if job.metrics_csv
            else None
        )

        service = CollectorService(settings, session)
        try:
            platform = PlatformName(job.platform)
        except ValueError:
            job_repo.finish(job.id, "failed", error=f"Unknown platform '{job.platform}'")
            return

        try:
            result = await service.collect_one(
                advertiser,
                platform,
                countries=countries,
                metrics=metrics,
                headless=headless,
                use_cdp=use_cdp,
            )
        except Exception as exc:
            logger.exception("Job %s failed: %s", job.id, exc)
            job_repo.finish(job.id, "failed", error=str(exc))
            return

        # Link the scrape_run created inside collect_one so MCP can show
        # partial progress via scrape_run_metrics.
        latest_run_id = session.scalar(
            text(
                "SELECT id FROM scrape_runs "
                "WHERE advertiser_name = :name AND platform = :platform "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"name": advertiser.name, "platform": job.platform},
        )
        if latest_run_id is not None:
            job_repo.attach_run(job.id, int(latest_run_id))

        status = result.status if result.status in ("success", "partial") else "failed"
        job_repo.finish(
            job.id,
            status,
            error=result.message if status == "failed" else None,
        )


def run_worker(
    *,
    poll_interval: float | None = None,
    platforms: list[str] | None = None,
    use_cdp: bool = True,
    headless: bool = False,
    max_jobs: int | None = None,
) -> None:
    settings = get_settings()
    session_factory = build_session_factory(settings)
    worker_id = _make_worker_id()
    interval = poll_interval or settings.worker_poll_interval_s

    logger.info(
        "Worker %s starting (platforms=%s, poll=%.1fs, use_cdp=%s)",
        worker_id,
        platforms or "any",
        interval,
        use_cdp,
    )

    processed = 0
    while True:
        with session_factory() as session:
            job = JobRepository(session).claim_next(worker_id=worker_id, platforms=platforms)
            job_id = job.id if job else None

        if job_id is None:
            time.sleep(interval)
            continue

        logger.info("Worker %s claimed job %s", worker_id, job_id)
        asyncio.run(
            _run_job(
                settings,
                session_factory,
                job_id,
                use_cdp=use_cdp,
                headless=headless,
            )
        )
        processed += 1
        if max_jobs is not None and processed >= max_jobs:
            logger.info("Worker %s reached max_jobs=%s; exiting", worker_id, max_jobs)
            return
