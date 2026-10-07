"""Transactional pre-run cost reservation and final usage accounting."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


class RunPermission(StrEnum):
    ALLOWED = "ALLOWED"
    WARN_SOFT_LIMIT = "WARN_SOFT_LIMIT"
    BLOCKED_HARD_LIMIT = "BLOCKED_HARD_LIMIT"


async def can_start_agent_run(
    conn: AsyncConnection, discussion_id: str, estimated_cost: float
) -> RunPermission:
    row = (
        (
            await conn.execute(
                text("""
        SELECT cost_total_usd, cost_reserved_usd, cost_soft_limit_usd,
               cost_hard_limit_usd, soft_limit_notified
          FROM discussions WHERE id=:discussion_id FOR UPDATE
    """),
                {"discussion_id": discussion_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError(f"Discussion {discussion_id} not found")
    projected = row["cost_total_usd"] + row["cost_reserved_usd"] + Decimal(str(estimated_cost))
    if projected > row["cost_hard_limit_usd"]:
        return RunPermission.BLOCKED_HARD_LIMIT
    if row["cost_total_usd"] >= row["cost_soft_limit_usd"] and not row["soft_limit_notified"]:
        return RunPermission.WARN_SOFT_LIMIT
    return RunPermission.ALLOWED


async def reserve_cost(conn: AsyncConnection, discussion_id: str, estimated_cost: float) -> None:
    await conn.execute(
        text("""
        UPDATE discussions SET cost_reserved_usd=cost_reserved_usd+:amount
         WHERE id=:discussion_id
    """),
        {"amount": Decimal(str(estimated_cost)), "discussion_id": discussion_id},
    )


async def record_actual_cost(
    conn: AsyncConnection,
    discussion_id: str,
    agent_run_id: str,
    agent_id: str,
    provider: str,
    model_used: str,
    tokens_input: int,
    tokens_output: int,
    actual_cost: float,
    estimated_cost: float,
) -> None:
    values = {
        "actual": Decimal(str(actual_cost)),
        "estimated": Decimal(str(estimated_cost)),
        "discussion_id": discussion_id,
        "run_id": agent_run_id,
        "agent_id": agent_id,
        "provider": provider,
        "model": model_used,
        "tokens_in": tokens_input,
        "tokens_out": tokens_output,
    }
    await conn.execute(
        text("""
        UPDATE discussions SET cost_total_usd=cost_total_usd+:actual,
          cost_reserved_usd=GREATEST(0,cost_reserved_usd-:estimated), updated_at=NOW()
        WHERE id=:discussion_id
    """),
        values,
    )
    await conn.execute(
        text("""
        UPDATE agent_runs SET cost_usd=:actual,tokens_input=:tokens_in,tokens_output=:tokens_out
        WHERE id=:run_id
    """),
        values,
    )
    await conn.execute(
        text("""
        INSERT INTO usage_costs(agent_run_id,discussion_id,agent_id,provider,model_used,
          tokens_input,tokens_output,cost_usd)
        VALUES(:run_id,:discussion_id,:agent_id,:provider,:model,:tokens_in,:tokens_out,:actual)
    """),
        values,
    )


async def mark_soft_limit_notified(conn: AsyncConnection, discussion_id: str) -> None:
    await conn.execute(
        text("UPDATE discussions SET soft_limit_notified=TRUE WHERE id=:discussion_id"),
        {"discussion_id": discussion_id},
    )
