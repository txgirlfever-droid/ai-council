"""End-to-end tests against a real PostgreSQL database (mock AI providers).

Webhook → durable event → job → council rounds → CEO review → decision button.

Skipped unless TEST_DATABASE_URL points at a disposable database, e.g.:

    TEST_DATABASE_URL=postgresql://council:secret@localhost:5432/ai_council_test \
        python -m pytest tests/test_postgres_e2e.py

WARNING: the database's public schema is dropped and recreated.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")

CEO_ID = 4242
CHAT_ID = -1001234
SECRET = "test-secret"
ROOT = Path(__file__).parents[1]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.answers: list[str] = []
        self.cleared: list[int] = []

    async def send_message(self, chat_id, text, *, topic_id=None, markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "markup": markup})
        return 9000 + len(self.sent)

    async def answer_callback(self, callback_query_id, text):
        self.answers.append(text)

    async def clear_buttons(self, chat_id, message_id):
        self.cleared.append(message_id)


class FakeAdapter:
    """Answers like a real provider would, as JSON text."""

    def __init__(self, agent_id: str, positions: dict[str, str], fail: bool = False):
        self.agent_id = agent_id
        self.positions = positions
        self.fail = fail
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000):
        from council.agents.base import ProviderAdapter

        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("provider down")
        position = self.positions.get("default", "OPTION_A")
        for marker, pos in self.positions.items():
            if marker != "default" and marker in prompt:
                position = pos
        body = {
            "summary": f"{self.agent_id} prefers {position}",
            "position": position,
            "recommendation": f"Go with {position}",
            "confidence": 0.7,
            "disagreements": []
            if position == "OPTION_A"
            else [{"agent_id": "codex", "point": "storage", "reason": "scaling"}],
            "risks": [
                {
                    "title": f"{self.agent_id} risk",
                    "likelihood": "LOW",
                    "impact": "HIGH",
                    "description": "test",
                }
            ],
            "questions": ["Expected user count?"],
        }
        raw = "```json\n" + json.dumps(body) + "\n```"
        return ProviderAdapter._parse_response(self, raw, model, "fake", 100, 50, 0.01)

    def estimate_cost(self, prompt: str, model: str) -> float:
        return 0.01


def make_factory(behaviour: dict[str, dict], failing: set[str] = frozenset()):
    provider_to_agent = {"openai": "codex", "anthropic": "claude", "google": "gemini"}
    adapters: dict[str, FakeAdapter] = {}

    def factory(provider: str):
        agent = provider_to_agent[provider]
        adapters[agent] = FakeAdapter(agent, behaviour.get(agent, {}), agent in failing)
        return adapters[agent]

    return factory, adapters


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def migrated_db():
    """Fresh schema via the real Alembic migration (also tests the migration)."""
    import asyncpg

    async def reset():
        conn = await asyncpg.connect(TEST_DB)
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.close()

    asyncio.run(reset())
    env = {**os.environ, "DATABASE_URL": TEST_DB}
    result = subprocess.run(
        [sys.executable, "-m", "council.db.migrate"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return TEST_DB


@pytest.fixture
async def app_env(migrated_db, monkeypatch):
    from council import db
    from council.config import settings
    from council.orchestrator import service
    from council.telegram import sender
    from council.worker import worker

    monkeypatch.setattr(settings, "database_url", migrated_db)
    monkeypatch.setattr(settings, "ceo_telegram_user_id", CEO_ID)
    monkeypatch.setattr(settings, "webhook_secret", SECRET)
    for key in ("openai_api_key", "anthropic_api_key", "google_api_key"):
        monkeypatch.setattr(settings, key, "test-key")
    monkeypatch.setattr(service, "PROVIDER_RETRIES", 0)
    worker.load_handlers()
    telegram = FakeTelegram()
    sender.set_client(telegram)

    from council.webhook.handler import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "telegram": telegram, "service": service}

    sender.set_client(None)
    await db.close_engine()


async def post_update(client, update: dict) -> int:
    response = await client.post(
        "/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )
    return response.status_code


async def drain_jobs() -> int:
    from council.db import get_engine
    from council.worker.worker import claim_job, process_job

    processed = 0
    while True:
        async with get_engine().begin() as conn:
            job = await claim_job(conn)
        if job is None:
            return processed
        await process_job(job)
        processed += 1


async def fetch(sql: str, **params):
    from sqlalchemy import text

    from council.db import get_engine

    async with get_engine().connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings()]


_next_update = iter(range(500_000, 600_000))


def command(text_: str, user_id: int = CEO_ID) -> dict:
    return {
        "update_id": next(_next_update),
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "username": "ceo"},
            "chat": {"id": CHAT_ID},
            "text": text_,
        },
    }


def press(callback_data: str, message_id: int) -> dict:
    uid = next(_next_update)
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"cb-{uid}",
            "from": {"id": CEO_ID},
            "data": callback_data,
            "message": {"message_id": message_id, "chat": {"id": CHAT_ID}},
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_council_to_ceo_decision_end_to_end(app_env, monkeypatch):
    client, telegram, service = app_env["client"], app_env["telegram"], app_env["service"]
    factory, adapters = make_factory(
        {
            "codex": {"default": "OPTION_A"},
            "claude": {"default": "OPTION_A"},
            # Gemini disagrees, then is persuaded in debate cycle 2.
            "gemini": {"default": "OPTION_B", "Debate cycle 2": "OPTION_A"},
        }
    )
    monkeypatch.setattr(service, "adapter_factory", factory)

    update = command("/council Postgres or MongoDB for the game backend?")
    assert await post_update(client, update) == 200
    assert await post_update(client, update) == 200  # Telegram retry → no second job
    assert await drain_jobs() == 1

    [disc] = await fetch("SELECT * FROM discussions")
    assert disc["base_state"] == "CEO_REVIEW"
    assert disc["question"] == "Postgres or MongoDB for the game backend?"
    rounds = await fetch(
        "SELECT round_type, state FROM rounds WHERE discussion_id=:d ORDER BY round_number",
        d=disc["id"],
    )
    assert [r["round_type"] for r in rounds] == [
        "INDEPENDENT_REVIEW",
        "CROSS_REVIEW",
        "DEBATE",
        "DEBATE",
        "SYNTHESIS",
    ]
    assert all(r["state"] == "COMPLETE" for r in rounds)
    runs = await fetch("SELECT * FROM agent_runs WHERE discussion_id=:d", d=disc["id"])
    assert len(runs) == 15 and all(r["status"] == "COMPLETED" for r in runs)
    assert all(r["prompt_version_id"] and r["context_snapshot_id"] for r in runs)
    [dis] = await fetch("SELECT * FROM disagreements")
    assert dis["status"] == "RESOLVED" and dis["cycles_completed"] == 2
    [costs] = await fetch("SELECT COUNT(*) AS n, SUM(cost_usd) AS total FROM usage_costs")
    assert costs["n"] == 15
    [disc] = await fetch("SELECT * FROM discussions")
    assert float(disc["cost_total_usd"]) == pytest.approx(0.15)
    assert float(disc["cost_reserved_usd"]) == 0
    # Agents were asked for the structured contract and saw each other's positions.
    assert "Reply with ONE JSON object" in adapters["claude"].prompts[0]
    assert "gemini: [OPTION_B]" in adapters["claude"].prompts[1]

    started, review_msg = telegram.sent
    assert "DISC-" in started["text"] and "started" in started["text"]
    assert "CEO review" in review_msg["text"]
    assert "FULL COUNCIL RESULT (3/3 agents)" in review_msg["text"]
    assert "UNANIMOUS" not in review_msg["text"]
    buttons = review_msg["markup"].inline_keyboard[0]
    approve, reject = buttons[0].callback_data, buttons[1].callback_data
    assert len(approve.encode()) <= 64  # Telegram callback_data limit

    # CEO double-taps: Approve then Reject → exactly one decision, the first.
    assert await post_update(client, press(approve, 9002)) == 200
    assert await post_update(client, press(reject, 9002)) == 200
    assert await drain_jobs() == 2
    decisions = await fetch("SELECT * FROM decisions")
    assert len(decisions) == 1 and decisions[0]["status"] == "APPROVED"
    assert decisions[0]["agent_recs_json"]  # agent recommendations kept with the decision
    [disc] = await fetch("SELECT * FROM discussions")
    assert disc["base_state"] == "APPROVED" and disc["closed_at"] is not None
    assert telegram.answers[0].startswith("Recorded") and "Already decided" in telegram.answers[1]
    assert telegram.cleared == [9002]
    assert "CEO approved" in telegram.sent[-1]["text"]

    # Decisions and audit log are append-only at the database level.
    from sqlalchemy import text

    from council.db import get_engine

    with pytest.raises(Exception, match="append-only"):
        async with get_engine().begin() as conn:
            await conn.execute(text("UPDATE decisions SET status='REJECTED'"))
    actions = {r["action"] for r in await fetch("SELECT action FROM audit_log")}
    assert {"DISCUSSION_CREATED", "CEO_REVIEW_OPENED", "DECISION_APPROVED"} <= actions
    events = await fetch("SELECT processing_status FROM telegram_events")
    assert {e["processing_status"] for e in events} == {"DONE"}


async def test_quorum_failure_pauses_instead_of_inventing_result(app_env, monkeypatch):
    client, telegram, service = app_env["client"], app_env["telegram"], app_env["service"]
    factory, _ = make_factory({}, failing={"claude", "gemini"})
    monkeypatch.setattr(service, "adapter_factory", factory)

    await post_update(client, command("/council Should we ship on Friday?"))
    assert await drain_jobs() == 1
    [disc] = await fetch("SELECT * FROM discussions WHERE question='Should we ship on Friday?'")
    assert disc["base_state"] == "PAUSED"
    assert "paused" in telegram.sent[-1]["text"] and "quorum" in telegram.sent[-1]["text"]
    assert not await fetch("SELECT 1 FROM ceo_reviews WHERE discussion_id=:d", d=disc["id"])


async def test_non_ceo_and_bad_secret_are_ignored(app_env):
    client = app_env["client"]
    before = await fetch("SELECT COUNT(*) AS n FROM job_queue")
    assert await post_update(client, command("/council hi", user_id=1)) == 200
    response = await client.post(
        "/webhook", json=command("/council hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "x"}
    )
    assert response.status_code == 403
    after = await fetch("SELECT COUNT(*) AS n FROM job_queue")
    assert after == before


async def test_status_and_help_commands(app_env):
    client, telegram = app_env["client"], app_env["telegram"]
    await post_update(client, command("/status"))
    await post_update(client, command("/help"))
    assert await drain_jobs() == 2
    assert telegram.sent[-2]["text"].startswith("📊 Queue")
    assert "/council <question>" in telegram.sent[-1]["text"]
