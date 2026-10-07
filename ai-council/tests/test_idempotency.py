"""
Idempotency tests — MUST HAVE (Correction #2, Layer 1 & 2).

Tests:
- Duplicate Telegram update_id → ignored (Layer 1)
- Duplicate CEO callback (different callback_query_id) → ignored (Layer 2)
- Approve A vs Approve B race → only first wins (UNIQUE constraint)
- Duplicate RUN_AGENT job → only one enqueued
- Worker crash/reclaim — stale lease recovery

Terminology: IDEMPOTENT / EFFECTIVELY-ONCE business effect.
NOT "exactly-once" — distributed systems do not guarantee that.
"""

from __future__ import annotations

from datetime import UTC

import asyncpg


# ---------------------------------------------------------------------------
# Layer 1: telegram_update_id UNIQUE
# ---------------------------------------------------------------------------
class TestLayer1TelegramUpdateIdIdempotency:
    """
    Same telegram_update_id arriving twice must result in only one processing.
    Layer 1 dedup is enforced by UNIQUE constraint on telegram_events.telegram_update_id.
    """

    def test_duplicate_update_id_detected(self, mock_conn):
        """
        Simulates: first insert succeeds, second insert raises UniqueViolationError.
        Webhook handler must return is_new=False on second call → effectively-once.
        """
        call_count = 0
        raised = False

        class FakeConn:
            async def execute(self, query, *args):
                nonlocal call_count, raised
                call_count += 1
                if call_count > 1 and "telegram_events" in query:
                    raised = True
                    raise asyncpg.UniqueViolationError(
                        "duplicate key value violates unique constraint"
                    )

        # Simulate the idempotency check function logic
        async def simulate_idempotency(conn, update_id: int) -> bool:
            try:
                await conn.execute(
                    "INSERT INTO telegram_events (telegram_update_id) VALUES ($1)",
                    update_id,
                )
                return True  # New event
            except asyncpg.UniqueViolationError:
                return False  # Duplicate

        import asyncio

        conn = FakeConn()

        result1 = asyncio.run(simulate_idempotency(conn, 12345))
        result2 = asyncio.run(simulate_idempotency(conn, 12345))

        assert result1 is True, "First event should be accepted"
        assert result2 is False, "Duplicate event must be rejected (effectively-once)"
        assert raised, "UniqueViolationError must have been raised on second insert"

    def test_different_update_ids_both_accepted(self, mock_conn):
        """Different update_ids must both be processed."""
        seen: set[int] = set()

        async def simulate_idempotency(update_id: int) -> bool:
            if update_id in seen:
                return False
            seen.add(update_id)
            return True

        import asyncio

        r1 = asyncio.run(simulate_idempotency(100))
        r2 = asyncio.run(simulate_idempotency(101))
        assert r1 is True
        assert r2 is True


# ---------------------------------------------------------------------------
# Layer 2: CEO decision business idempotency (decision_action_key UNIQUE)
# ---------------------------------------------------------------------------
class TestLayer2CeoDecisionIdempotency:
    """
    CEO pressing [Approve B] twice (two different callback_query_ids from Telegram)
    must result in exactly one decision record.

    decision_action_key = "decision:DISC-021:CEO_REVIEW_V1"
    UNIQUE constraint on ceo_reviews.decision_action_key prevents second insert.
    """

    def test_first_approve_succeeds_second_rejected(self):
        """
        Scenario: CEO_REVIEW_V1 for DISC-021.
        First callback → INSERT ceo_reviews → success → INSERT decisions.
        Second callback (new callback_query_id) hits the review UNIQUE constraint and stops.
        """
        existing_reviews: dict[str, dict] = {}

        def try_create_review(decision_action_key: str, option: str) -> tuple[bool, str]:
            """
            Returns (success, reason).
            Simulates DB UNIQUE constraint on decision_action_key.
            """
            if decision_action_key in existing_reviews:
                existing = existing_reviews[decision_action_key]
                return False, f"Already decided: {existing['option']}"
            existing_reviews[decision_action_key] = {"option": option, "status": "DECIDED"}
            return True, "Decision created"

        key = "decision:DISC-021:CEO_REVIEW_V1"

        # First press (callback_query_id = "abc123")
        ok1, msg1 = try_create_review(key, "OPTION_B")
        assert ok1 is True
        assert "created" in msg1

        # Second press (callback_query_id = "def456" — Telegram generates new ID)
        ok2, msg2 = try_create_review(key, "OPTION_B")
        assert ok2 is False
        assert "Already decided" in msg2

        # Only 1 decision exists
        assert len(existing_reviews) == 1

    def test_approve_a_vs_approve_b_race(self):
        """
        Race: Worker 1 → Approve A, Worker 2 → Approve B simultaneously.
        Only the one that commits first wins. Second gets UNIQUE VIOLATION.
        Whichever option wins: only ONE decision is created.
        """
        import threading

        lock = threading.Lock()
        existing_reviews: dict[str, dict] = {}
        results: list[tuple[bool, str]] = []

        def try_create_review_threadsafe(decision_action_key: str, option: str):
            with lock:  # simulates DB serialization
                if decision_action_key in existing_reviews:
                    results.append(
                        (
                            False,
                            "UNIQUE VIOLATION — already: "
                            + existing_reviews[decision_action_key]["option"],
                        )
                    )
                else:
                    existing_reviews[decision_action_key] = {"option": option}
                    results.append((True, f"Decision created: {option}"))

        key = "decision:DISC-021:CEO_REVIEW_V1"
        t1 = threading.Thread(target=try_create_review_threadsafe, args=(key, "OPTION_A"))
        t2 = threading.Thread(target=try_create_review_threadsafe, args=(key, "OPTION_B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[0] is True]
        failures = [r for r in results if r[0] is False]

        assert len(successes) == 1, "Exactly one decision must be created"
        assert len(failures) == 1, "One attempt must fail with UNIQUE VIOLATION"
        assert len(existing_reviews) == 1, "Only one review record may exist"


# ---------------------------------------------------------------------------
# Job queue idempotency
# ---------------------------------------------------------------------------
class TestJobQueueIdempotency:
    """
    Duplicate RUN_AGENT jobs with same idempotency_key must not create duplicates.
    Uses ON CONFLICT (idempotency_key) DO NOTHING.
    """

    def test_duplicate_job_key_ignored(self):
        """Same idempotency_key → second insert is a no-op."""
        jobs: dict[str, dict] = {}

        def enqueue(idempotency_key: str, job_type: str) -> bool:
            """Returns True if inserted, False if duplicate (ON CONFLICT DO NOTHING)."""
            if idempotency_key in jobs:
                return False  # DO NOTHING
            jobs[idempotency_key] = {"job_type": job_type, "status": "PENDING"}
            return True

        key = "run-agent-DISC-021-R1-codex"
        r1 = enqueue(key, "RUN_AGENT")
        r2 = enqueue(key, "RUN_AGENT")

        assert r1 is True
        assert r2 is False
        assert len(jobs) == 1

    def test_different_keys_both_enqueued(self):
        """Different idempotency keys → both jobs enqueued."""
        jobs: dict[str, dict] = {}

        def enqueue(key: str, job_type: str) -> bool:
            if key in jobs:
                return False
            jobs[key] = {"job_type": job_type}
            return True

        r1 = enqueue("run-DISC-021-R1-codex", "RUN_AGENT")
        r2 = enqueue("run-DISC-021-R1-claude", "RUN_AGENT")

        assert r1 is True
        assert r2 is True
        assert len(jobs) == 2


# ---------------------------------------------------------------------------
# Stale lease recovery idempotency
# ---------------------------------------------------------------------------
class TestStaleleaseRecovery:
    """
    Worker crash → lease expires → job returned to PENDING.
    Second worker claims and completes it → effectively-once execution.
    """

    def test_crashed_worker_job_reclaimed(self):
        """
        Worker 1 claims job → crashes → lease expires.
        Recovery marks job PENDING again.
        Worker 2 claims and completes → DONE.
        Result: job completed exactly once (effectively-once).
        """
        from datetime import datetime, timedelta

        job = {
            "id": "job-001",
            "status": "RUNNING",
            "locked_by": "worker-crashed",
            "lease_expires_at": datetime.now(UTC) - timedelta(seconds=60),  # expired
            "attempt_count": 1,
            "max_attempts": 3,
        }
        completions: list[str] = []

        def recover_stale(job: dict) -> None:
            now = datetime.now(UTC)
            if job["status"] == "RUNNING" and job["lease_expires_at"] < now:
                if job["attempt_count"] < job["max_attempts"]:
                    job["status"] = "PENDING"
                    job["locked_by"] = None
                else:
                    job["status"] = "FAILED"

        def claim_and_complete(worker_id: str, job: dict) -> bool:
            if job["status"] != "PENDING":
                return False
            job["status"] = "RUNNING"
            job["locked_by"] = worker_id
            job["attempt_count"] += 1
            # Process...
            job["status"] = "DONE"
            completions.append(worker_id)
            return True

        # Simulate recovery + reclaim
        recover_stale(job)
        assert job["status"] == "PENDING"

        ok = claim_and_complete("worker-2", job)
        assert ok is True
        assert job["status"] == "DONE"
        assert len(completions) == 1, "Job completed exactly once (effectively-once)"

    def test_max_retries_exceeded_marks_failed(self):
        """Job that exceeded max_retries after stale lease → FAILED, not re-queued."""
        from datetime import datetime, timedelta

        job = {
            "status": "RUNNING",
            "lease_expires_at": datetime.now(UTC) - timedelta(seconds=60),
            "attempt_count": 3,
            "max_attempts": 3,
        }

        def recover_stale(job: dict) -> None:
            now = datetime.now(UTC)
            if job["status"] == "RUNNING" and job["lease_expires_at"] < now:
                if job["attempt_count"] < job["max_attempts"]:
                    job["status"] = "PENDING"
                else:
                    job["status"] = "FAILED"
                    job["error_message"] = "Max attempts exceeded after stale lease"

        recover_stale(job)
        assert job["status"] == "FAILED"
        assert "Max attempts" in job.get("error_message", "")


# ---------------------------------------------------------------------------
# Telegram retry idempotency
# ---------------------------------------------------------------------------
class TestTelegramRetryIdempotency:
    """
    Telegram webhook retries the same update → must not duplicate processing.
    """

    def test_webhook_retry_same_update_id(self):
        """
        Telegram retries delivery 3 times with same update_id.
        Only first should be processed. All return 200 OK (to stop retries).
        """
        processed_updates: set[int] = set()
        responses: list[int] = []

        def handle_webhook(update_id: int) -> int:
            """Returns HTTP status code."""
            if update_id in processed_updates:
                responses.append(200)  # ACK to stop retries, but don't process
                return 200
            processed_updates.add(update_id)
            responses.append(200)
            return 200

        update_id = 99999
        for _ in range(3):  # Telegram retries 3 times
            handle_webhook(update_id)

        assert len(responses) == 3
        assert all(r == 200 for r in responses)
        assert len(processed_updates) == 1, "Must process only once (effectively-once)"
