"""
Append-only audit log. NO updates, NO deletes.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def log(
    conn: AsyncConnection,
    *,
    actor: str,
    action: str,
    actor_user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """
    Append one audit entry. Immutable after insert.
    actor: 'CEO', 'codex', 'system', etc.
    """
    await conn.execute(
        text("""
        INSERT INTO audit_log
          (actor, actor_user_id, action, entity_type, entity_id, detail_json)
        VALUES (:actor, :actor_user_id, :action, :entity_type, :entity_id,
                CAST(:detail AS JSONB))
        """),
        {
            "actor": actor,
            "actor_user_id": actor_user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "detail": json.dumps(detail) if detail else None,
        },
    )
