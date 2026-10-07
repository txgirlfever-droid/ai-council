"""FastAPI Telegram webhook with two-layer idempotency."""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import FastAPI, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from council.audit.log import log as audit_log
from council.config import settings
from council.db import get_engine
from council.webhook.service import update_job_key

logger = logging.getLogger(__name__)
app = FastAPI(title="AI Council", version="0.2.1")


def verify_ceo(telegram_user_id: int) -> bool:
    return telegram_user_id == settings.ceo_telegram_user_id


def verify_webhook_secret(received: str | None) -> bool:
    return not settings.webhook_secret or received == settings.webhook_secret


def _event(update: dict) -> tuple[str, int]:
    if callback := update.get("callback_query"):
        return "CALLBACK", int(callback.get("from", {}).get("id", 0))
    if message := update.get("message"):
        kind = "COMMAND" if str(message.get("text", "")).startswith("/") else "MESSAGE"
        return kind, int(message.get("from", {}).get("id", 0))
    return "UNKNOWN", 0


async def record_event(conn: AsyncConnection, update: dict, event_type: str, user_id: int) -> bool:
    nested = await conn.begin_nested()
    try:
        await conn.execute(
            text("""
            INSERT INTO telegram_events
                (telegram_update_id, event_type, from_user_id, raw_payload,
                 payload_hash, processing_status)
            VALUES (:update_id, :event_type, :user_id, CAST(:payload AS JSONB),
                    :payload_hash, 'RECEIVED')
        """),
            {
                "update_id": update["update_id"],
                "event_type": event_type,
                "user_id": user_id,
                "payload": json.dumps(update),
                "payload_hash": hashlib.sha256(
                    json.dumps(update, sort_keys=True).encode()
                ).hexdigest(),
            },
        )
        await nested.commit()
        return True
    except IntegrityError:
        await nested.rollback()
        return False


async def enqueue_update(conn: AsyncConnection, update: dict, event_type: str) -> None:
    message_text = str((update.get("message") or {}).get("text", ""))
    job_type = "COMPLETE_ROUND" if message_text.startswith("/council") else "NOTIFY_CEO"
    await conn.execute(
        text("""
        INSERT INTO job_queue (job_type, payload, idempotency_key, priority)
        VALUES (:job_type, CAST(:payload AS JSONB), :key, 3)
        ON CONFLICT (idempotency_key) DO NOTHING
    """),
        {
            "payload": json.dumps({"event_type": event_type, "update": update}),
            "key": update_job_key(update),
            "job_type": job_type,
        },
    )


@app.post("/webhook")
async def handle_webhook(request: Request) -> Response:
    if not verify_webhook_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        return Response(status_code=403)
    try:
        update = await request.json()
    except Exception:
        return Response(status_code=400)
    event_type, user_id = _event(update)
    if event_type == "UNKNOWN" or not verify_ceo(user_id):
        return Response(status_code=200)

    async with get_engine().begin() as conn:
        if not await record_event(conn, update, event_type, user_id):
            return Response(status_code=200)
        await enqueue_update(conn, update, event_type)
        await audit_log(
            conn,
            actor="CEO",
            action=f"TELEGRAM_{event_type}",
            entity_type="telegram_event",
            entity_id=str(update["update_id"]),
        )
    return Response(status_code=200)


@app.get("/status")
async def status() -> dict:
    async with get_engine().connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT COUNT(*) FILTER (WHERE status='PENDING') AS pending_jobs,
                   COUNT(*) FILTER (WHERE status='RUNNING') AS running_jobs
              FROM job_queue
        """)
                )
            )
            .mappings()
            .one()
        )
    return {"status": "ok", **dict(row), "agent_execution_timeout_s": 180, "round_timeout_s": 600}


@app.get("/healthz")
async def health() -> dict:
    try:
        async with get_engine().connect() as conn:
            pending = (
                await conn.execute(text("SELECT COUNT(*) FROM job_queue WHERE status='PENDING'"))
            ).scalar_one()
        return {"status": "ok", "queue_depth": pending}
    except Exception:
        logger.exception("Health check failed")
        return {"status": "error"}
