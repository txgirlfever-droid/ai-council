"""Lease transition rules shared by the PostgreSQL worker and unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class Lease:
    status: str = "PENDING"
    locked_by: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3

    def claim(self, worker_id: str, lease_seconds: int, now: datetime | None = None) -> bool:
        if self.status != "PENDING":
            return False
        now = now or datetime.now(UTC)
        self.status, self.locked_by = "RUNNING", worker_id
        self.heartbeat_at = now
        self.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.attempt_count += 1
        return True

    def heartbeat(self, worker_id: str, lease_seconds: int, now: datetime | None = None) -> bool:
        if self.status != "RUNNING" or self.locked_by != worker_id:
            return False
        now = now or datetime.now(UTC)
        self.heartbeat_at = now
        self.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return True

    def recover(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.status != "RUNNING" or not self.lease_expires_at or self.lease_expires_at >= now:
            return False
        self.status = "PENDING" if self.attempt_count < self.max_attempts else "FAILED"
        self.locked_by = None
        self.heartbeat_at = None
        self.lease_expires_at = None
        return True
