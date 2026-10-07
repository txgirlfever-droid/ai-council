"""Framework-independent Telegram intake and deterministic idempotency keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def update_job_key(update: dict) -> str:
    update_id = update.get("update_id")
    if update_id is None:
        payload = json.dumps(update, sort_keys=True, separators=(",", ":"))
        return "telegram:" + hashlib.sha256(payload.encode()).hexdigest()
    return f"telegram:{update_id}"


def decision_action_key(discussion_id: str, review_id: str) -> str:
    return f"decision:{discussion_id}:{review_id}"


@dataclass
class IntakeLedger:
    seen_updates: set[int] = field(default_factory=set)
    jobs: dict[str, dict] = field(default_factory=dict)

    def accept(self, update: dict) -> bool:
        update_id = int(update["update_id"])
        if update_id in self.seen_updates:
            return False
        self.seen_updates.add(update_id)
        self.jobs.setdefault(update_job_key(update), update)
        return True
