"""PostgreSQL job worker with atomic claims, heartbeat, and lease recovery."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from council.config import settings
from council.db import get_engine

logger = logging.getLogger(__name__)
WORKER_ID = str(uuid.uuid4())
JobHandler = Callable[[dict], Awaitable[None]]
JOB_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str):
    def decorator(fn: JobHandler) -> JobHandler:
        JOB_HANDLERS[job_type] = fn
        return fn

    return decorator


async def claim_job(conn: AsyncConnection) -> dict | None:
    result = await conn.execute(
        text("""
        WITH claimable AS (
            SELECT id FROM job_queue
             WHERE status='PENDING' AND scheduled_at <= NOW()
             ORDER BY priority, scheduled_at
             LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        UPDATE job_queue j
           SET status='RUNNING', locked_by=:worker_id, locked_at=NOW(),
               lease_expires_at=NOW() + make_interval(secs => :lease_seconds),
               heartbeat_at=NOW(), started_at=COALESCE(started_at, NOW()),
               attempt_count=attempt_count + 1
          FROM claimable c WHERE j.id=c.id
        RETURNING j.id, j.job_type, j.payload
    """),
        {"worker_id": WORKER_ID, "lease_seconds": settings.worker_lease_seconds},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def recover_stale_leases(conn: AsyncConnection) -> None:
    await conn.execute(
        text("""
        UPDATE job_queue SET
            status=CASE WHEN attempt_count < max_attempts THEN 'PENDING' ELSE 'FAILED' END,
            error_message=CASE WHEN attempt_count >= max_attempts
                THEN 'Max attempts exceeded after stale lease' ELSE error_message END,
            locked_by=NULL, locked_at=NULL, lease_expires_at=NULL, heartbeat_at=NULL
        WHERE status='RUNNING' AND lease_expires_at < NOW()
    """)
    )


async def heartbeat_once(conn: AsyncConnection) -> int:
    result = await conn.execute(
        text("""
        UPDATE job_queue SET heartbeat_at=NOW(),
            lease_expires_at=NOW() + make_interval(secs => :lease_seconds)
        WHERE status='RUNNING' AND locked_by=:worker_id
    """),
        {"worker_id": WORKER_ID, "lease_seconds": settings.worker_lease_seconds},
    )
    return result.rowcount


async def heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(settings.worker_heartbeat_seconds)
        try:
            async with get_engine().begin() as conn:
                await heartbeat_once(conn)
        except Exception:
            logger.exception("Worker heartbeat failed")


async def _finish(job_id: uuid.UUID, *, error: str | None = None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("""
            UPDATE job_queue SET status=:status, completed_at=NOW(), error_message=:error,
                locked_by=NULL, lease_expires_at=NULL, heartbeat_at=NULL
            WHERE id=:job_id AND locked_by=:worker_id
        """),
            {
                "status": "FAILED" if error else "DONE",
                "error": error,
                "job_id": job_id,
                "worker_id": WORKER_ID,
            },
        )


async def process_job(job: dict) -> None:
    handler = JOB_HANDLERS.get(job["job_type"])
    if handler is None:
        await _finish(job["id"], error=f"Unknown job type: {job['job_type']}")
        return
    try:
        await handler(job["payload"])
    except Exception as exc:
        logger.exception("Job %s failed", job["id"])
        await _finish(job["id"], error=str(exc))
    else:
        await _finish(job["id"])


async def worker_loop() -> None:
    while True:
        try:
            async with get_engine().begin() as conn:
                await recover_stale_leases(conn)
                job = await claim_job(conn)
            if job is None:
                await asyncio.sleep(1)
            else:
                await process_job(job)
        except Exception:
            logger.exception("Worker loop failed")
            await asyncio.sleep(2)


async def run_worker() -> None:
    await asyncio.gather(worker_loop(), heartbeat_loop())
